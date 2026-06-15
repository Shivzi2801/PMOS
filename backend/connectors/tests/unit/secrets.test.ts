import { describe, it, expect } from 'vitest';
import { LocalKmsProvider } from '../../secrets/kms/LocalKmsProvider';
import { SecretCrypto } from '../../secrets/service/SecretCrypto';
import { SecretService } from '../../secrets/service/SecretService';
import { InMemorySecretAuditSink } from '../../secrets/service/SecretAudit';
import { SecretRef } from '../../secrets/model/SecretModel';
import { MemSecretStore, FakeClock } from '../fakes';

const ref: SecretRef = { tenantId: 't1', connectionId: 'c1', name: 'oauth' };
const audit = { actor: 'system', correlationId: 'corr-1' };

function makeService() {
  const kms = new LocalKmsProvider();
  const crypto = new SecretCrypto(kms);
  const store = new MemSecretStore();
  const sink = new InMemorySecretAuditSink();
  const clock = new FakeClock();
  const service = new SecretService(store, crypto, sink, clock, 5 * 60 * 1000);
  return { service, store, sink, clock };
}

describe('SecretCrypto envelope encryption', () => {
  it('round-trips plaintext through KMS-enveloped ciphertext', async () => {
    const crypto = new SecretCrypto(new LocalKmsProvider());
    const plaintext = Buffer.from('super-secret-token', 'utf8');
    const env = await crypto.encrypt('t1', Buffer.from(plaintext));
    expect(env.ciphertext.equals(plaintext)).toBe(false); // actually encrypted
    expect(env.wrappedDek.length).toBeGreaterThan(0);
    const out = await crypto.decrypt(env);
    expect(out.toString('utf8')).toBe('super-secret-token');
  });

  it('fails to decrypt a tampered ciphertext (GCM auth tag)', async () => {
    const crypto = new SecretCrypto(new LocalKmsProvider());
    const env = await crypto.encrypt('t1', Buffer.from('abc'));
    const tampered = { ...env, ciphertext: Buffer.from(env.ciphertext) };
    tampered.ciphertext[0] ^= 0xff;
    await expect(crypto.decrypt(tampered)).rejects.toBeInstanceOf(Error);
  });
});

describe('SecretService', () => {
  it('writes then reads back the secret', async () => {
    const { service } = makeService();
    await service.write(ref, Buffer.from('tok-1'), audit, { expiresAt: 10_000 });
    const out = await service.read(ref, audit);
    expect(out.toString('utf8')).toBe('tok-1');
  });

  it('useSecret scopes plaintext and zeroes it after', async () => {
    const { service } = makeService();
    await service.write(ref, Buffer.from('tok-zero'), audit);
    let captured: Buffer | undefined;
    await service.useSecret(ref, audit, async (pt) => {
      captured = pt;
      expect(pt.toString('utf8')).toBe('tok-zero');
    });
    // After the callback, the buffer must be zeroed.
    expect(captured!.every((b) => b === 0)).toBe(true);
  });

  it('rotation creates a new ACTIVE version and supersedes the old', async () => {
    const { service, store } = makeService();
    await service.write(ref, Buffer.from('v1'), audit);
    await service.rotate(ref, Buffer.from('v2'), audit);
    const versions = await store.listVersions(ref);
    expect(versions).toHaveLength(2);
    expect(versions[0].version).toBe(2);
    expect(versions[0].status).toBe('ACTIVE');
    expect(versions[1].status).toBe('SUPERSEDED');
    const active = await service.read(ref, audit);
    expect(active.toString('utf8')).toBe('v2');
  });

  it('emits audit events for write + read', async () => {
    const { service, sink } = makeService();
    await service.write(ref, Buffer.from('x'), audit);
    await service.read(ref, audit);
    const actions = sink.events.map((e) => e.action);
    expect(actions).toContain('WRITE');
    expect(actions).toContain('READ');
    expect(sink.events.every((e) => e.outcome === 'SUCCESS')).toBe(true);
  });

  it('shouldRotate is true within the lead window and false outside', async () => {
    const { service, clock } = makeService();
    // expires 4 minutes from clock now; lead window is 5 minutes => should rotate
    const soon = clock.now().getTime() + 4 * 60 * 1000;
    await service.write(ref, Buffer.from('exp'), audit, { expiresAt: soon });
    expect(await service.shouldRotate(ref)).toBe(true);

    const far = clock.now().getTime() + 60 * 60 * 1000;
    await service.rotate(ref, Buffer.from('exp2'), audit, { expiresAt: far });
    expect(await service.shouldRotate(ref)).toBe(false);
  });

  it('revoke marks all versions REVOKED and read then fails', async () => {
    const { service, store } = makeService();
    await service.write(ref, Buffer.from('a'), audit);
    await service.revoke(ref, audit, 'disconnect');
    const versions = await store.listVersions(ref);
    expect(versions.every((v) => v.status === 'REVOKED')).toBe(true);
    await expect(service.read(ref, audit)).rejects.toBeTruthy();
  });

  it('records FAILURE audit when reading a missing secret', async () => {
    const { service, sink } = makeService();
    await expect(service.read(ref, audit)).rejects.toBeTruthy();
    expect(sink.events.some((e) => e.action === 'READ' && e.outcome === 'FAILURE')).toBe(true);
  });
});
