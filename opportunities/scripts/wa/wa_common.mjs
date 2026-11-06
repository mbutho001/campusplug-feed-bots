// Shared bits for the WhatsApp Channel poster: the encrypted linked-device session and the socket.
//
// The session (Baileys multi-file auth state) is what lets the bot act as a linked device of
// the phone that owns the "Campus Plug SA" channel, so it is never stored in the clear: the
// files are bundled, gzipped and AES-256-GCM encrypted into state/wa-auth.enc with WA_AUTH_KEY
// (64 hex characters, a GitHub secret). The private state repo only ever sees ciphertext.
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import zlib from "node:zlib";
import { fileURLToPath } from "node:url";
import makeWASocket, { Browsers, DisconnectReason, fetchLatestBaileysVersion, useMultiFileAuthState } from "@whiskeysockets/baileys";
import pino from "pino";

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const BLOB = process.env.WA_AUTH_BLOB || path.resolve(HERE, "../../state/wa-auth.enc");

function key() {
  const hex = (process.env.WA_AUTH_KEY || "").trim();
  if (!/^[0-9a-f]{64}$/i.test(hex)) throw new Error("WA_AUTH_KEY must be 64 hex characters");
  return Buffer.from(hex, "hex");
}

export function unpack(dir) {
  // First cloud run: the blob isn't in the state repo yet, so it comes from the WA_AUTH_SEED secret
  // (base64 of the blob wa_link.mjs made). After that the refreshed blob in state/ wins.
  if (!fs.existsSync(BLOB) && process.env.WA_AUTH_SEED) {
    fs.mkdirSync(path.dirname(BLOB), { recursive: true });
    fs.writeFileSync(BLOB, Buffer.from(process.env.WA_AUTH_SEED.trim(), "base64"), { mode: 0o600 });
  }
  if (!fs.existsSync(BLOB)) return false;
  const raw = fs.readFileSync(BLOB);
  const iv = raw.subarray(0, 12), tag = raw.subarray(12, 28), body = raw.subarray(28);
  const d = crypto.createDecipheriv("aes-256-gcm", key(), iv);
  d.setAuthTag(tag);
  const files = JSON.parse(zlib.gunzipSync(Buffer.concat([d.update(body), d.final()])).toString());
  for (const [name, content] of Object.entries(files)) fs.writeFileSync(path.join(dir, name), content);
  return true;
}

export function pack(dir) {
  const files = {};
  for (const name of fs.readdirSync(dir)) files[name] = fs.readFileSync(path.join(dir, name), "utf8");
  const iv = crypto.randomBytes(12);
  const c = crypto.createCipheriv("aes-256-gcm", key(), iv);
  const body = Buffer.concat([c.update(zlib.gzipSync(JSON.stringify(files))), c.final()]);
  fs.mkdirSync(path.dirname(BLOB), { recursive: true });
  fs.writeFileSync(BLOB, Buffer.concat([iv, c.getAuthTag(), body]), { mode: 0o600 });
}

export function tempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "wa-auth-"));
}

// Connect and resolve once the socket is open. onPairing(sock) runs on a fresh, unregistered
// session (linking); restarts WhatsApp asks for (after pairing) are followed automatically.
export async function connect(dir, { onPairing, onQr, timeoutMs = 120000 } = {}) {
  const { state, saveCreds } = await useMultiFileAuthState(dir);
  const { version } = await fetchLatestBaileysVersion().catch(() => ({ version: undefined }));
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const sock = makeWASocket({
      auth: state, version, logger: pino({ level: process.env.WA_LOG || "silent" }), printQRInTerminal: false,
      browser: Browsers.ubuntu("Chrome"), markOnlineOnConnect: false, syncFullHistory: false,
    });
    sock.ev.on("creds.update", saveCreds);
    let asked = false;
    const result = await new Promise((resolve) => {
      sock.ev.on("connection.update", async (u) => {
        if (u.qr && onQr) await onQr(u.qr);
        if (u.qr && !state.creds.registered && onPairing && !asked) {
          asked = true;
          await onPairing(sock);
        }
        if (u.connection === "open") resolve({ ok: true });
        if (u.connection === "close") resolve({ ok: false, code: u.lastDisconnect?.error?.output?.statusCode });
      });
    });
    if (result.ok) return { sock, saveCreds };
    if (result.code === DisconnectReason.loggedOut) throw new Error("WhatsApp logged this device out - link it again (wa_link.mjs)");
    if (Date.now() > deadline) throw new Error(`could not connect to WhatsApp (last code ${result.code})`);
    await new Promise((r) => setTimeout(r, 2000));   // restartRequired after pairing, or a blip
  }
}

export async function channelJid(sock, ref) {
  ref = (ref || "").trim().replace(/^https?:\/\/(www\.)?whatsapp\.com\/channel\//, "").replace(/\/$/, "");
  if (ref.endsWith("@newsletter")) {
    const meta = await sock.newsletterMetadata("jid", ref);
    return { jid: ref, name: meta?.name ?? meta?.thread_metadata?.name?.text };
  }
  const meta = await sock.newsletterMetadata("invite", ref);
  if (!meta?.id) throw new Error(`no channel found for invite ${ref}`);
  return { jid: meta.id, name: meta.name ?? meta.thread_metadata?.name?.text };
}
