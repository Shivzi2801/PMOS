import { createHmac, timingSafeEqual } from 'node:crypto';
import { DataIntegrityError, ValidationError } from '../../sdk/errors/ConnectorError';
import { TenantContext } from '../../sdk/interfaces/types';

/**
 * Zendesk webhook handling.
 *
 * Zendesk signs webhook deliveries with HMAC-SHA256 over
 * `${timestamp}${rawBody}` using a per-webhook signing secret, sending:
 *   - X-Zendesk-Webhook-Signature: base64(HMAC-SHA256(...))
 *   - X-Zendesk-Webhook-Signature-Timestamp: ISO-8601 timestamp
 *
 * Verification is constant-time and rejects deliveries whose timestamp is
 * outside a tolerance window (replay protection). The signing secret is a
 * tenant secret stored KMS-enveloped under the name "webhook_signing"; the
 * orchestrator resolves it via SecretService and passes it in here, so this
 * module stays pure.
 *
 * On a verified delivery we normalize the event into a `ZendeskWebhookEvent`
 * which the orchestrator can use to trigger a targeted incremental sync rather
 * than trusting the payload as source of truth (defense in depth).
 */

export const DEFAULT_REPLAY_TOLERANCE_MS = 5 * 60 * 1000;

export interface WebhookDelivery {
  readonly rawBody: string;
  readonly signature: string;
  readonly timestamp: string;
}

export interface ZendeskWebhookEvent {
  readonly type: string;
  readonly detail: Record<string, unknown>;
  readonly receivedAt: string;
}

export interface VerifyOptions {
  readonly toleranceMs?: number;
  readonly now?: () => number;
}

function computeSignature(signingSecret: string, timestamp: string, rawBody: string): string {
  return createHmac('sha256', signingSecret)
    .update(`${timestamp}${rawBody}`)
    .digest('base64');
}

/** Constant-time compare of two base64 signatures. */
function signaturesEqual(a: string, b: string): boolean {
  const ba = Buffer.from(a);
  const bb = Buffer.from(b);
  if (ba.length !== bb.length) return false;
  return timingSafeEqual(ba, bb);
}

export class ZendeskWebhookVerifier {
  /**
   * Verify and parse a delivery. Throws ValidationError on signature/timestamp
   * failure and DataIntegrityError on unparseable bodies.
   */
  verify(
    context: TenantContext,
    delivery: WebhookDelivery,
    signingSecret: string,
    options: VerifyOptions = {},
  ): ZendeskWebhookEvent {
    const now = options.now ? options.now() : Date.now();
    const tolerance = options.toleranceMs ?? DEFAULT_REPLAY_TOLERANCE_MS;

    const ts = Date.parse(delivery.timestamp);
    if (Number.isNaN(ts)) {
      throw new ValidationError('Webhook timestamp invalid', {
        tenantId: context.tenantId,
        connectionId: context.connectionId,
        operation: 'zendesk.webhook.verify',
      });
    }
    if (Math.abs(now - ts) > tolerance) {
      throw new ValidationError('Webhook timestamp outside tolerance (possible replay)', {
        tenantId: context.tenantId,
        connectionId: context.connectionId,
        operation: 'zendesk.webhook.verify',
        detail: { skewMs: now - ts },
      });
    }

    const expected = computeSignature(signingSecret, delivery.timestamp, delivery.rawBody);
    if (!signaturesEqual(expected, delivery.signature)) {
      throw new ValidationError('Webhook signature mismatch', {
        tenantId: context.tenantId,
        connectionId: context.connectionId,
        operation: 'zendesk.webhook.verify',
      });
    }

    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(delivery.rawBody) as Record<string, unknown>;
    } catch (err) {
      throw new DataIntegrityError('Webhook body is not valid JSON', {
        tenantId: context.tenantId,
        connectionId: context.connectionId,
        operation: 'zendesk.webhook.verify',
      }, err);
    }

    const type = typeof parsed['type'] === 'string' ? (parsed['type'] as string) : 'unknown';
    return {
      type,
      detail: parsed,
      receivedAt: new Date(now).toISOString(),
    };
  }
}

export const __test__ = { computeSignature };
