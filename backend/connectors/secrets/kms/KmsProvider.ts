/**
 * KMS provider abstraction.
 *
 * Envelope encryption model:
 *   - A per-tenant Key Encryption Key (KEK) lives in the cloud KMS and never
 *     leaves it. We reference it by `keyId`.
 *   - For each secret write we generate a fresh Data Encryption Key (DEK),
 *     encrypt the plaintext locally with the DEK (AES-256-GCM), then ask KMS
 *     to encrypt the DEK under the tenant KEK. We persist the ciphertext, the
 *     KMS-wrapped DEK, the IV, and the auth tag. The plaintext DEK is zeroed
 *     from memory immediately after use.
 *   - On read we ask KMS to unwrap the DEK, decrypt locally, then zero the DEK.
 *
 * This keeps KMS call volume low (one small wrap/unwrap per secret op) while
 * ensuring no DEK or plaintext is ever persisted, and per-tenant key isolation
 * is enforced cryptographically.
 */

export interface GenerateDataKeyResult {
  /** Plaintext DEK bytes. Caller MUST zero after use. */
  readonly plaintextKey: Buffer;
  /** DEK encrypted under the tenant KEK; safe to persist. */
  readonly encryptedKey: Buffer;
  /** The KEK key id / ARN that wrapped the DEK. */
  readonly keyId: string;
}

export interface KmsProvider {
  /**
   * Generate a fresh 256-bit DEK, returning both plaintext and KEK-wrapped
   * forms. `keyId` selects the tenant KEK.
   */
  generateDataKey(keyId: string): Promise<GenerateDataKeyResult>;

  /**
   * Unwrap a previously KEK-encrypted DEK. `keyId` MUST match the KEK used to
   * wrap it (mismatch => the provider throws).
   */
  decryptDataKey(keyId: string, encryptedKey: Buffer): Promise<Buffer>;

  /**
   * Resolve (and lazily create) the KEK alias/id for a tenant. The mapping
   * tenant -> KEK is owned by the KMS provider so rotation of the underlying
   * key material is transparent to callers.
   */
  resolveTenantKeyId(tenantId: string): Promise<string>;
}
