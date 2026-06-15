import { createCipheriv, createDecipheriv, randomBytes } from 'node:crypto';
import { GenerateDataKeyResult, KmsProvider } from './KmsProvider';

/**
 * LocalKmsProvider
 *
 * A self-contained KMS implementation for local development and tests. It
 * simulates a cloud KMS by holding a single in-process master key and deriving
 * a deterministic per-tenant KEK from it. The wire format of the wrapped DEK
 * matches what a real provider would produce (iv || tag || ciphertext), so the
 * SecretService code path is identical in dev and prod.
 *
 * DO NOT use in production: the master key lives in process memory. The
 * AwsKmsProvider (KmsProvider implemented against AWS KMS) is the production
 * binding and is wired via dependency injection.
 */

const ALG = 'aes-256-gcm';
const IV_LEN = 12;
const TAG_LEN = 16;

export class LocalKmsProvider implements KmsProvider {
  private readonly masterKey: Buffer;

  constructor(masterKey?: Buffer) {
    // 32 bytes = AES-256. Generated per-process if not supplied.
    this.masterKey = masterKey ?? randomBytes(32);
    if (this.masterKey.length !== 32) {
      throw new Error('LocalKmsProvider master key must be 32 bytes');
    }
  }

  async resolveTenantKeyId(tenantId: string): Promise<string> {
    // In a real KMS this returns a key ARN/alias. Here we namespace by tenant.
    return `local-kek/${tenantId}`;
  }

  /**
   * Derive a stable per-tenant KEK from the master key + keyId. Uses HKDF-like
   * construction via crypto. Deterministic so unwrap works across calls.
   */
  private deriveKek(keyId: string): Buffer {
    // Simple, deterministic derivation: AES-encrypt the keyId label under the
    // master key in a fixed-IV ECB-free manner using GCM with a zero IV is
    // unsafe in general, so we use an HMAC-style KDF instead.
    const { createHmac } = require('node:crypto') as typeof import('node:crypto');
    return createHmac('sha256', this.masterKey).update(`kek:${keyId}`).digest();
  }

  async generateDataKey(keyId: string): Promise<GenerateDataKeyResult> {
    const plaintextKey = randomBytes(32); // 256-bit DEK
    const kek = this.deriveKek(keyId);
    const iv = randomBytes(IV_LEN);
    const cipher = createCipheriv(ALG, kek, iv);
    const ct = Buffer.concat([cipher.update(plaintextKey), cipher.final()]);
    const tag = cipher.getAuthTag();
    const encryptedKey = Buffer.concat([iv, tag, ct]);
    return { plaintextKey, encryptedKey, keyId };
  }

  async decryptDataKey(keyId: string, encryptedKey: Buffer): Promise<Buffer> {
    const kek = this.deriveKek(keyId);
    const iv = encryptedKey.subarray(0, IV_LEN);
    const tag = encryptedKey.subarray(IV_LEN, IV_LEN + TAG_LEN);
    const ct = encryptedKey.subarray(IV_LEN + TAG_LEN);
    const decipher = createDecipheriv(ALG, kek, iv);
    decipher.setAuthTag(tag);
    return Buffer.concat([decipher.update(ct), decipher.final()]);
  }
}
