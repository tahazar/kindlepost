# kindlepost

Share an ebook from your iPhone → it's converted with Calibre → your phone emails it to your Kindle.

The server only converts files. Sending happens from your phone's Mail app, so no email passwords are stored on the server.

## How it works
- **EPUB, PDF, DOCX, TXT, RTF, HTML**: Amazon accepts these directly. EPUBs are still round-tripped through Calibre by default, which fixes most "E999" rejections and gives a cover page Amazon renders reliably.
- **MOBI, AZW3, FB2, CBZ/CBR, LIT, ODT, Markdown, etc.**: converted to EPUB first (Amazon no longer wants MOBI).
- Results over ~48 MB are refused (Amazon's email limit is 50 MB); use amazon.com/sendtokindle for those.

## Setup
1. **Approve your sending address**: Amazon → Manage Your Content and Devices → Preferences → Personal Document Settings → add the email account your iPhone's Mail app will send from to the approved list. Note your `@kindle.com` address there too.
2. `cp .env.example .env` and set `API_TOKEN` (`openssl rand -hex 24`).
3. `docker compose up -d --build`, then check `curl localhost:8080/health`.
   - **On a Mac**: install Docker Desktop or OrbStack, then run `./bootstrap-mac.sh`. It creates `.env` with a random token, builds and starts the container, waits for `/health`, and prints the URLs. Get the token with `docker compose exec kindlepost printenv API_TOKEN`. Set `HOST_PORT` in `.env` if port 8080 is taken. The container restarts with Docker, so set Docker to start at login and stop the Mac sleeping (System Settings → Energy) if your phone should reach it anytime.
4. **Make it reachable from your phone**: easiest is Tailscale on the server and phone (use the `100.x` IP or MagicDNS name). Alternatively a Cloudflare Tunnel or a small VPS with HTTPS.

Test from a laptop:
```sh
curl -OJ -H "Authorization: Bearer $API_TOKEN" -F file=@book.mobi http://SERVER:8080/convert
```

## iOS Shortcut ("Send to Kindle")
1. New Shortcut → tap the **i** → enable **Show in Share Sheet**; input type **Files**.
2. **Get Contents of URL**
   - URL: `http://SERVER:8080/convert`
   - Method: **POST**
   - Headers: `Authorization` = `Bearer YOUR_TOKEN`
   - Request Body: **Form** → field `file`, type **File**, value **Shortcut Input**
3. **Get Details of Files** → **File Extension** of *Contents of URL*.
4. **If** *File Extension* **is** `json` → **Show Result** (*Contents of URL*) → **Stop This Shortcut** → **End If**. (Errors come back as JSON, so this stops a failed conversion from being emailed.)
5. **Send Email**
   - Recipients: `yourname@kindle.com`
   - From: the approved account
   - Attachment / body: *Contents of URL*
   - Turn **Show Compose Sheet** off to send without tapping
6. **Show Notification**: "Sent to Kindle".

Now in Files, Safari downloads, Mail attachments, etc.: Share → **Send to Kindle**.

**Link variant**: duplicate the shortcut, point it at `/convert-url`, and make the form field `url` (Text) = Shortcut Input.

**Optional prompts**: add **Ask for Input** actions before step 2 and pass the results as `title` / `author` form fields to override metadata.

There's also a simple upload page at `http://SERVER:8080/` that converts a file and downloads the result.
