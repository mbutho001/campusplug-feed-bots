// Post to the Campus Plug SA WhatsApp Channel as a linked device.
//
//   node wa_post.mjs --caption-file roundup.txt a.png b.png c.png   # images, caption on the first
//   node wa_post.mjs --check                                         # connect, name the channel
//
// Env: WA_AUTH_KEY (decrypts state/wa-auth.enc), WA_CHANNEL (invite code, channel link or
// ...@newsletter id). The refreshed session is re-encrypted back into the blob on exit.
import fs from "node:fs";
import { channelJid, connect, pack, tempDir, unpack } from "./wa_common.mjs";

const args = process.argv.slice(2);
const check = args.includes("--check");
const capIdx = args.indexOf("--caption-file");
const caption = capIdx >= 0 ? fs.readFileSync(args[capIdx + 1], "utf8") : "";
const images = args.filter((a, i) => !a.startsWith("--") && !(capIdx >= 0 && i === capIdx + 1));

const dir = tempDir();
let sock;
try {
  if (!unpack(dir)) throw new Error("no linked session (state/wa-auth.enc) - run wa_link.mjs first");
  ({ sock } = await connect(dir));
  const { jid, name } = await channelJid(sock, process.env.WA_CHANNEL);
  if (check) {
    console.log(`linked as ${sock.user?.id?.split(":")[0]}, channel "${name}" (${jid})`);
  } else {
    if (!images.length && !caption) throw new Error("nothing to post");
    let first;
    if (!images.length) {
      first = await sock.sendMessage(jid, { text: caption });
    }
    for (const [n, img] of images.entries()) {
      const msg = await sock.sendMessage(jid, { image: fs.readFileSync(img), ...(n === 0 && caption ? { caption } : {}) });
      first ??= msg;
      await new Promise((r) => setTimeout(r, 1500));
    }
    await new Promise((r) => setTimeout(r, 4000));   // let the last upload settle before closing
    console.log(`posted ${first?.key?.id ?? "?"} to "${name}"`);
  }
} catch (e) {
  console.error(`whatsapp error: ${e.message}`);
  process.exitCode = 1;
} finally {
  try { sock?.end?.(undefined); } catch {}
  try { if (fs.readdirSync(dir).length) pack(dir); } catch (e) { console.error(`could not save session: ${e.message}`); }
  fs.rmSync(dir, { recursive: true, force: true });
  setTimeout(() => process.exit(), 500);
}
