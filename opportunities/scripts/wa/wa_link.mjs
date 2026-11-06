// Link the poster to the phone that owns the Campus Plug SA channel (run once, on the home PC).
//
//   node wa_link.mjs --phone 27831234567 [--channel <invite link or code>]
//
// Prints an 8-character pairing code: on the phone open WhatsApp -> Settings -> Linked devices
// -> Link a device -> "Link with phone number instead" and type it. The session is encrypted
// into state/wa-auth.enc. The key comes from WA_AUTH_KEY, or is made and saved to
// ~/.config/campusplug/wa-auth.key (0600) - put the same value in the GitHub secret WA_AUTH_KEY.
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { BLOB, channelJid, connect, pack, tempDir } from "./wa_common.mjs";

const args = process.argv.slice(2);
const opt = (n) => { const i = args.indexOf(n); return i >= 0 ? args[i + 1] : undefined; };
const phone = (opt("--phone") || "").replace(/\D/g, "");
if (!/^27\d{9}$/.test(phone)) {
  console.error("usage: node wa_link.mjs --phone 27XXXXXXXXX [--channel <invite>]");
  process.exit(2);
}

const keyFile = path.join(os.homedir(), ".config/campusplug/wa-auth.key");
if (!process.env.WA_AUTH_KEY) {
  if (!fs.existsSync(keyFile)) {
    fs.mkdirSync(path.dirname(keyFile), { recursive: true });
    fs.writeFileSync(keyFile, crypto.randomBytes(32).toString("hex"), { mode: 0o600 });
  }
  process.env.WA_AUTH_KEY = fs.readFileSync(keyFile, "utf8").trim();
}

const dir = tempDir();
let sock;
try {
  ({ sock } = await connect(dir, {
    timeoutMs: 300000,
    onPairing: async (s) => {
      const code = await s.requestPairingCode(phone);
      console.log(`PAIRING CODE: ${code.slice(0, 4)}-${code.slice(4)}`);
      console.log("WhatsApp -> Settings -> Linked devices -> Link a device -> Link with phone number instead");
    },
  }));
  await new Promise((r) => setTimeout(r, 8000));   // let the first key sync land
  console.log(`linked as ${sock.user?.id?.split(":")[0]}`);
  const ch = opt("--channel") || process.env.WA_CHANNEL;
  if (ch) {
    const { jid, name } = await channelJid(sock, ch);
    console.log(`channel "${name}" = ${jid}`);
  }
} catch (e) {
  console.error(`link failed: ${e.message}`);
  process.exitCode = 1;
} finally {
  try { sock?.end?.(undefined); } catch {}
  if (fs.readdirSync(dir).length) { pack(dir); console.log(`session saved (encrypted) to ${BLOB}`); }
  fs.rmSync(dir, { recursive: true, force: true });
  setTimeout(() => process.exit(), 500);
}
