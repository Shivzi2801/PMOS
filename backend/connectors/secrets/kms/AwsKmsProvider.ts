import { GenerateDataKeyResult, KmsProvider } from './KmsProvider';

/**
 * AwsKmsProvider
 *
 * Production KMS binding. Uses AWS KMS GenerateDataKey / Decrypt with a
 * per-tenant KEK addressed by alias `alias/pmos/tenant/<tenantId>`. The DEK
 * never leaves this process in plaintext beyond the lifetime of a single
 * encrypt/decrypt and is zeroed by the caller (SecretCrypto).
 *
 * The AWS SDK is injected rather than imported directly so this file has no
 * hard dependency at the SDK layer and remains unit-testable. The concrete
 * client is provided at composition time.
 *
 * Expected client shape (subset of @aws-sdk/client-kms):
 *   generateDataKey({ KeyId, KeySpec }): Promise<{ Plaintext, CiphertextBlob, KeyId }>
 *   decrypt({ KeyId, CiphertextBlob }): Promise<{ Plaintext }>
 *   createAlias / describeKey for resolveTenantKeyId provisioning.
 */

export interface AwsKmsClientLike {
  generateDataKey(input: {
    KeyId: string;
    KeySpec: 'AES_256';
  }): Promise<{ Plaintext?: Uint8Array; CiphertextBlob?: Uint8Array; KeyId?: string }>;

  decrypt(input: {
    KeyId: string;
    CiphertextBlob: Uint8Array;
  }): Promise<{ Plaintext?: Uint8Array }>;
}

export interface AwsKmsProviderOptions {
  /** Function that maps tenantId -> KEK alias/ARN. Must be provisioned ahead of time. */
  readonly aliasForTenant: (tenantId: string) => string;
}

export class AwsKmsProvider implements KmsProvider {
  constructor(
    private readonly client: AwsKmsClientLike,
    private readonly options: AwsKmsProviderOptions,
  ) {}

  async resolveTenantKeyId(tenantId: string): Promise<string> {
    return this.options.aliasForTenant(tenantId);
  }

  async generateDataKey(keyId: string): Promise<GenerateDataKeyResult> {
    const res = await this.client.generateDataKey({ KeyId: keyId, KeySpec: 'AES_256' });
    if (!res.Plaintext || !res.CiphertextBlob) {
      throw new Error('AWS KMS GenerateDataKey returned no key material');
    }
    return {
      plaintextKey: Buffer.from(res.Plaintext),
      encryptedKey: Buffer.from(res.CiphertextBlob),
      keyId: res.KeyId ?? keyId,
    };
  }

  async decryptDataKey(keyId: string, encryptedKey: Buffer): Promise<Buffer> {
    const res = await this.client.decrypt({ KeyId: keyId, CiphertextBlob: encryptedKey });
    if (!res.Plaintext) {
      throw new Error('AWS KMS Decrypt returned no plaintext');
    }
    return Buffer.from(res.Plaintext);
  }
}
