# FlowSpace — working project workspace

## Start on your Mac

1. Extract the ZIP.
2. Open Terminal in the `flowspace` folder (or type `cd ` and drag that folder into Terminal).
3. Set up the local UI/UX demo and start:

   ```bash
   cp .env.example .env
   python3 server.py
   ```

4. Open **http://localhost:8000** in Safari or Chrome.
5. Click **Create an account**, enter your details and two recovery answers, then enter the six-digit code shown in the demo panel.

**Demo mode does not send or verify real emails.** Read `EMAIL_SETUP.md` to configure actual email delivery. Without explicit demo mode or SMTP configuration, OTP-dependent signup/login remains blocked.

Keep Terminal running while using the app. Press Control+C to stop it. To start again, run the same command. Python 3.10 or newer is required; no npm, pip packages, Docker, external API keys or internet connection is required to run this app. If `python3` is missing, install Python 3 from python.org first.

**Do not open index.html by double-clicking it.** The app needs its Python server for login and database access. You can also launch `Start-FlowSpace.command` on macOS if executable permissions are retained; Terminal is the reliable fallback.

If port 8000 is occupied:

```bash
PORT=8001 python3 server.py
```

Then open http://localhost:8001.

## Pages and actions

| Page | What works |
|---|---|
| Overview | Personalized greeting, live metrics, projects, your tasks, activity |
| Projects | Create, edit, delete, favorite, filter, view linked tasks; completion from tasks |
| My Tasks | Create, edit, delete, assign to members, link to projects, filter, drag between columns or change status with keyboard-accessible dropdown |
| Analytics | Live completion line graph (7/30 days), task status pie chart, priority/member bar charts, accessible chart data, CSV download |
| Messages | Workspace chat with actual member accounts, saved history, refresh every 5 seconds |
| Team | Members, pending invitations, revoke invitations, remove members (owner only) |
| Calendar | Real month grid, previous/next month, today, day agenda, create/edit/delete events, task/project deadlines |
| Settings | Save profile name, light/dark theme, workspace name (owner only), change password |
| Login / registration | Verified email signup, email OTP after 24 hours, expiring server sessions, login/logout |
| Forgot password | Email OTP, security answers, password reset, previous sessions revoked |

Every named section has its own HTML page and URL. Refresh and browser back/forward work. Navigation adapts to mobile widths. User-entered content is escaped before rendering. Calendar times are local wall-clock times; this version does not convert times for teammates in other timezones.

## Your saved data

The server creates `flowspace.db` alongside `server.py`. Keep this file to retain accounts and workspace data. Your workspace starts empty so displayed figures reflect actual work rather than the original mockup's sample statistics. The original supplied files have not been overwritten.

To back up: stop the server and copy `flowspace.db` somewhere safe. Copy it back alongside `server.py` to restore. Do not delete it unless you want to reset all accounts and workspaces.

## Try team messages with two accounts

1. On the owner account, go to Team → Invite member and enter an unused email.
2. Open the app in a private/incognito browser window.
3. Create an account using exactly the invited email. The account joins the inviting workspace.
4. Open Messages in both browser windows. Send a message in one; it appears in the other within 5 seconds.

No invitation email is sent. This version supports one workspace per account. Existing accounts cannot be invited into another workspace; invite an email that has not yet registered. Removing a member gives them a new empty workspace and reassigns their tasks to the owner.

By default, only your computer can access the app. For trusted teammates on your same local network, run:

```bash
FLOWSPACE_HOST=0.0.0.0 python3 server.py
```

Share `http://YOUR_COMPUTER_LOCAL_IP:8000` with them. Allow access in your local firewall if asked. This is a local working application; public hosting needs HTTPS, operational safeguards, and deployment configuration. Messages do not send external emails or connect to Slack. There are no billing/payment features.

## Validation

Run `python3 test_server.py` and `python3 test_auth.py` to check authentication, workspace isolation, CSRF protection, saved project/task/event operations, invites, messages, settings and logout. Tests use their own temporary database and do not change your data.

The frontend is plain HTML/CSS/JavaScript. The backend uses Python's standard library and SQLite. There is no build step.

## Version 2 design and security

Refined dark green navigation, spacious cards, a new editorial login/sign-up/recovery design, responsive layouts, keyboard controls, and SVG graphs/pie charts with real task data. No external font or chart downloads are required. Chart tooltips work on pointer hover; readable legends and a chart-data table provide accessible alternatives.

Accounts from version one are migrated automatically. Their previous sessions are invalidated until email is verified. Keep your existing database when upgrading; details are in EMAIL_SETUP.md. OTP uses email, not SMS. Security answers are hashed and supplement OTP. SMTP must be configured for actual mailbox verification.

Full browser visual QA could not be run in this environment; backend integration and frontend renderer checks were completed.

## Version 3 polish

- Signup asks for a mobile number with country code; Settings lets you update it. The number is private to your account. OTP still uses email; collecting a phone number does not verify that number.
- Theme controls are available on login, signup, recovery, and in the app header. The browser preference is applied before paint, and signed-in changes are saved to your account.
- Branded initial loading, an unobtrusive request indicator, submit spinners, keyboard focus restoration, dismissible feedback, input error states, and animated modal open/close states.
- Responsive spacing and layouts from phones to large desktops, with reduced-motion support and mobile-friendly form text sizes.
- The “UI/UX demo mode / Test codes appear on screen. No real email is sent.” notice is below “◇ Email verification · Thoughtful account protection” on authentication pages.
- A port conflict prints a clear alternative startup command instead of a traceback.

To update without losing work: stop the old server, copy your existing `.env` and `flowspace.db` into this extracted folder, then run `PORT=8001 python3 server.py` and open http://localhost:8001. Do not overwrite your existing `.env` if you configured SMTP. The database migrates automatically. Restart once after an upgrade; browser refresh loads the new styles and scripts.
