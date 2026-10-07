# Email verification & account recovery

## Try the complete UI/UX flow locally

In the extracted `flowspace` folder:

```bash
cp .env.example .env
python3 server.py
```

Open http://localhost:8000 and create an account. The demo configuration **does not send emails**: a prominent banner and OTP screen display the test code. Use that code to preview signup, repeat-login verification, and password recovery. This tests the interaction design; it does not prove that an email address exists or belongs to the user.

Without SMTP configuration or explicit demo mode, signup and any login requiring OTP are blocked. There is no silent bypass.

## Enable actual email delivery

Open `.env` in a text editor. Set `FLOWSPACE_DEMO_MAIL=0` and fill in your email provider's SMTP settings:

```text
FLOWSPACE_DEMO_MAIL=0
SMTP_HOST=YOUR_PROVIDER_SMTP_HOST
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_FROM=YOUR_VERIFIED_SENDER_ADDRESS
SMTP_USERNAME=YOUR_SMTP_USERNAME
SMTP_PASSWORD=YOUR_SMTP_PASSWORD_OR_APP_PASSWORD
```

Use your provider's actual values. The sender must be authorized by the provider. If the provider uses implicit TLS, use `SMTP_SECURITY=ssl` and its corresponding port (commonly 465). Credentials stay on the server and are never included in browser JavaScript. Do not share `.env` or commit it to Git.

Restart the server after changing `.env`. Real mode sends a six-digit OTP to the supplied address and never returns the code to the browser. Check the inbox and spam folder. To confirm delivery, test with a mailbox you control; we have not sent live emails on your behalf.

## How verification works

- Signup validates address syntax, then sends a code. The account and workspace are created **only after the code is verified**. Sending a code and receiving it proves mailbox control; it cannot certify a user's legal identity.
- Codes expire in 10 minutes, allow at most five attempts, can be resent after 60 seconds, and are single-use. Resending invalidates older codes. Requests are throttled by account and IP.
- Sessions expire no later than 24 hours after the most recent successful OTP. A new login after that requires the correct password and a new email OTP. A logout older than 24 hours also triggers the check. Logging in again within the verified 24-hour window only requires the password.
- Forgot password first requires the email OTP, then two security answers and a new password. A successful reset signs out all previous sessions.
- Security questions are chosen during signup. Answers are stored as salted hashes, normalized for case and repeated spaces. They cannot be viewed again; replace them in Settings by confirming the current password.
- Existing version-one accounts and project data migrate automatically. Their first new login requires email verification. Add recovery questions in Settings. Until they do, recovery uses their verified email code alone, and the UI explains this legacy behavior.

Email OTP is implemented. SMS OTP is not included because it needs an SMS provider and verified phone enrollment. Security questions supplement email OTP; they are not used alone to authorize a password reset.

## Data migration

If you used the earlier app, stop its server and copy your existing `flowspace.db` into the upgraded folder before starting. Keep a backup. The upgrade adds tables and columns automatically; projects, tasks, events, messages and memberships remain.

## Public hosting

The app defaults to localhost. Public deployment needs HTTPS and `FLOWSPACE_SECURE_COOKIE=1`, plus deployment and email-service configuration. Keep `.env` and `flowspace.db` private. Do not use demo mode for real users. OTP requires network access to your SMTP service. There is no email-provider subscription included.

## Mobile number

New registrations require a mobile number including country code (for India, +91 followed by a valid 10-digit mobile format). Numbers are stored in international format and editable in Settings. Existing accounts retain access and can add a number in Settings. The number is not verified by SMS and is not used for OTP in this email-only version.
