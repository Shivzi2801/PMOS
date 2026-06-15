import { createCipheriv, createDecipheriv, randomBytes } from 'node:crypto';
import { KmsProvider } from '../kms/KmsProvider';

/**
 * SecretCrypto
 *
 * Performs the local half of envelope encryption: encrypts plaintext with a
 * fresh KMS-issued DEK using AES-256-GCM, and decrypts using a KMS-unwrapped
 * DEK. The plaintext DEK is zeroed from memory immediately after use in both
 * directions.
 *
 * Persisted envelope fields (all opaque blobs, base64 in the DB):
 *   - ciphertext: AES-GCM ciphertext of the secret plaintext
 *   - iv: 12-byte GCM nonce
 *   - authTag: 16-byte GCM auth tag
 *   - wrappedDek: DEK encrypted under the tenant KEK (from KMS)
 *   - keyId: KEK id used (for audit + rotation tracking)
 */

const ALG = 'aes-256-gcm';
const IV_LEN = 12;

export interface SecretEnvelope {
  readonly ciphertext: Buffer;
  readonly iv: Buffer;
  readonly authTag: Buffer;
  readonly wrappedDek: Buffer;
  readonly keyId: string;
}

function zero(buf: Buffer): void {
  buf.fill(0);
}

export class SecretCrypto {
  constructor(private readonly kms: KmsProvider) {}

  /**
   * Encrypt `plaintext` for `tenantId`. Returns a fully self-describing
   * envelope safe to persist. Never persists or returns the DEK.
   */
  async encrypt(tenantId: string, plaintext: Buffer): Promise<SecretEnvelope> {
    const keyId = await this.kms.resolveTenantKeyId(tenantId);
    const dek = await this.kms.generateDataKey(keyId);
    try {
      const iv = randomBytes(IV_LEN);
      const cipher = createCipheriv(ALG, dek.plaintextKey, iv);
      const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final()]);
      const authTag = cipher.getAuthTag();
      return {
        ciphertext,
        iv,
        authTag,
        wrappedDek: dek.encryptedKey,
        keyId: dek.keyId,
      };
    } finally {
      zero(dek.plaintextKey);
    }
  }

  /**
   * Decrypt an envelope back to plaintext. The caller is responsible for
   * zeroing the returned plaintext once consumed.
   */
  async decrypt(envelope: SecretEnvelope): Promise<Buffer> {
    const dek = await this.kms.decryptDataKey(envelope.keyId, envelope.wrappedDek);
    try {
      const decipher = createDecipheriv(ALG, dek, envelope.iv);
      decipher.setAuthTag(envelope.authTag);
      return Buffer.concat([decipher.update(envelope.ciphertext), decipher.final()]);
    } finally {
      zero(dek);
    }
  }
}
