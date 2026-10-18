# kindlepost

Share an ebook from your iPhone → it's converted with Calibre → emailed to your Kindle.

## How it works
- **EPUB, PDF, DOCX, TXT, RTF, HTML**: Amazon accepts these directly and converts them itself. EPUBs are still round-tripped through Calibre by default, which fixes most "E999" rejections.
- **MOBI, AZW3, FB2, CBZ/CBR, LIT, ODT, Markdown, etc.**: converted to EPUB first (Amazon no longer wants MOBI).
- Files over ~48 MB are rejected (Amazon's email limit is 50 MB); use amazon.com/sendtokindle for those.

## Setup
1. **Approve the sender**: Amazon → Manage Your Content and Devices → Preferences → Personal Document Settings → add `SMTP_USER` to the approved email list. Note your `@kindle.com` address there too.
2. **Gmail app password**: Google Account → Security → 2-Step Verification → App passwords.
3. `cp .env.example .env`, fill it in (`openssl rand -hex 24` for the token).
4. `docker compose up -d --build`, then check `curl localhost:8080/health`.
5. **Make it reachable from your phone**: easiest is Tailscale on the server and phone (use the `100.x` IP or MagicDNS name). Alternatively a Cloudflare Tunnel or a small VPS with HTTPS.

Test from a laptop:
```sh
curl -H "Authorization: Bearer $API_TOKEN" -F file=@book.mobi http://SERVER:8080/send
```

## iOS Shortcut ("Send to Kindle")
1. New Shortcut → tap the **i** → enable **Show in Share Sheet**; set input types to **Files** (and **URLs** if you want the link version).
2. Add **Get Contents of URL**:
   - URL: `http://SERVER:8080/send`
   - Method: **POST**
   - Headers: `Authorization` = `Bearer YOUR_TOKEN`
   - Request Body: **Form** → add field `file`, type **File**, value **Shortcut Input**
3. Add **Get Dictionary Value** → key `message` (from Contents of URL).
4. Add **Show Notification** → Dictionary Value.

Now in Files, Safari downloads, Mail attachments, etc.: Share → **Send to Kindle**.

**Link variant**: duplicate the shortcut, point it at `/send-url`, and make the form field `url` (Text) = Shortcut Input. Useful for a direct download link to an ebook.

**Optional prompts**: add **Ask for Input** actions before step 2 and pass the results as `title` / `author` form fields to override metadata.

There's also a simple upload page at `http://SERVER:8080/` for use from any browser.
