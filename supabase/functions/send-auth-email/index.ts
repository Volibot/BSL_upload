import { serve } from "https://deno.land/std@0.168.0/http/server.ts";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin":  "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

const TENANT_ID        = Deno.env.get("MICROSOFT_TENANT_ID")!;
const CLIENT_ID        = Deno.env.get("MICROSOFT_CLIENT_ID")!;
const CLIENT_SECRET    = Deno.env.get("MICROSOFT_CLIENT_SECRET")!;
const SENDER_EMAIL     = Deno.env.get("SENDER_EMAIL")!;
const SUPABASE_URL     = Deno.env.get("SUPABASE_URL")!;
const TEAMS_WEBHOOK    = Deno.env.get("RESUPD_TEAMS_WEBHOOK_URL") ?? "";

async function getAzureToken(): Promise<string> {
  const res = await fetch(
    `https://login.microsoftonline.com/${TENANT_ID}/oauth2/v2.0/token`,
    {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type:    "client_credentials",
        client_id:     CLIENT_ID,
        client_secret: CLIENT_SECRET,
        scope:         "https://graph.microsoft.com/.default",
      }),
    }
  );
  const data = await res.json();
  if (!data.access_token) throw new Error("Failed to get Azure token");
  return data.access_token;
}

interface Attachment { name: string; contentBytes: string; }

async function sendEmail(azureToken: string, to: string | string[], subject: string, html: string, cc: string[] = [], senderEmail?: string, attachments: Attachment[] = [], replyTo?: string, fromName?: string) {
  const sender     = senderEmail || SENDER_EMAIL;
  const toList     = Array.isArray(to) ? to : [to];
  const message: Record<string, unknown> = {
    subject,
    body: { contentType: "HTML", content: html },
    toRecipients: toList.map(addr => ({ emailAddress: { address: addr } })),
    from: { emailAddress: { address: sender, ...(fromName ? { name: fromName } : {}) } },
  };
  if (cc.length > 0) {
    message.ccRecipients = cc.map(addr => ({ emailAddress: { address: addr } }));
  }
  if (replyTo) {
    message.replyTo = [{ emailAddress: { address: replyTo } }];
  }
  if (attachments.length > 0) {
    message.attachments = attachments.map(a => ({
      "@odata.type": "#microsoft.graph.fileAttachment",
      name: a.name,
      contentBytes: a.contentBytes,
    }));
  }
  const res = await fetch(
    `https://graph.microsoft.com/v1.0/users/${sender}/sendMail`,
    {
      method: "POST",
      headers: {
        Authorization:  `Bearer ${azureToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ message }),
    }
  );
  if (!res.ok) throw new Error(`Graph API error: ${await res.text()}`);
}

function inviteHtml(confirmationUrl: string): string {
  return `
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f1f5f9;margin:0;padding:24px;">
<div style="max-width:560px;margin:0 auto;background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.1);">
  <div style="background:#0f52ba;padding:28px 32px;text-align:center;">
    <div style="font-size:28px;margin-bottom:8px;">📤</div>
    <h1 style="color:#fff;font-size:20px;font-weight:700;margin:0;">Resume Upload Portal</h1>
    <p style="color:rgba(255,255,255,.75);font-size:13px;margin:6px 0 0;">Client Candidate Submission · VOLIBITS</p>
  </div>
  <div style="padding:32px;">
    <p style="color:#334155;font-size:15px;margin:0 0 6px;">You've been granted access to the <strong>Resume Upload Portal</strong>.</p>
    <p style="color:#64748b;font-size:14px;margin:0 0 20px;">Please find the portal link and usage steps below.</p>
    <p style="color:#334155;font-size:14px;margin:0 0 4px;"><strong>Portal Link:</strong></p>
    <p style="margin:0 0 28px;"><a href="http://resupd.volibits.com/" style="color:#0f52ba;font-size:14px;">http://resupd.volibits.com/</a></p>
    <hr style="border:none;border-top:1px solid #e2e8f0;margin:0 0 24px;">
    <h2 style="font-size:15px;font-weight:700;color:#1e293b;margin:0 0 18px;">How to use the portal</h2>
    <div style="margin-bottom:16px;">
      <div style="font-size:13px;font-weight:700;color:#0f52ba;margin-bottom:5px;">Step 1 — Select the Job Requisition (JR)</div>
      <ul style="margin:0;padding-left:18px;color:#475569;font-size:13px;line-height:1.7;">
        <li>Choose the appropriate JR number from the dropdown list.</li>
        <li>Review the required skills and job details for the selected position.</li>
      </ul>
      <div style="background:#eff6ff;border-left:3px solid #3b82f6;padding:10px 14px;border-radius:0 6px 6px 0;margin-top:10px;">
        <p style="margin:0;font-size:12.5px;color:#1e40af;line-height:1.6;"><strong>Note</strong> – You can search in the JR Number field with the skillset as well &amp; check for any other resources that you have on bench or you can source from market based on your expertise.</p>
      </div>
    </div>
    <div style="margin-bottom:16px;">
      <div style="font-size:13px;font-weight:700;color:#0f52ba;margin-bottom:5px;">Step 2 — Enter Your Email Address</div>
      <ul style="margin:0;padding-left:18px;color:#475569;font-size:13px;line-height:1.7;">
        <li>Provide your work email address in the Recruiter Email field.</li>
        <li>You will receive upload confirmations and status updates on this email.</li>
      </ul>
    </div>
    <div style="margin-bottom:16px;">
      <div style="font-size:13px;font-weight:700;color:#0f52ba;margin-bottom:5px;">Step 3 — Upload Candidate Resume(s)</div>
      <ul style="margin:0;padding-left:18px;color:#475569;font-size:13px;line-height:1.7;">
        <li>Drag and drop resume file(s) into the upload area, or click to browse.</li>
        <li>Supported formats: PDF and DOCX.</li>
        <li>Multiple resumes can be uploaded at the same time.</li>
      </ul>
    </div>
    <div style="margin-bottom:16px;">
      <div style="font-size:13px;font-weight:700;color:#0f52ba;margin-bottom:5px;">Step 4 — Parse Resumes</div>
      <ul style="margin:0;padding-left:18px;color:#475569;font-size:13px;line-height:1.7;">
        <li>Resumes are parsed automatically on upload.</li>
        <li>You can also click <strong>"Parse All Resumes"</strong> to re-process if needed.</li>
      </ul>
    </div>
    <div style="margin-bottom:16px;">
      <div style="font-size:13px;font-weight:700;color:#0f52ba;margin-bottom:5px;">Step 5 — Review Candidate Information</div>
      <ul style="margin:0;padding-left:18px;color:#475569;font-size:13px;line-height:1.7;">
        <li>Name, Email Address, and Phone Number are populated automatically.</li>
        <li>Review the details and make corrections if needed before submitting.</li>
      </ul>
    </div>
    <div style="margin-bottom:24px;">
      <div style="font-size:13px;font-weight:700;color:#0f52ba;margin-bottom:5px;">Step 6 — Submit</div>
      <ul style="margin:0;padding-left:18px;color:#475569;font-size:13px;line-height:1.7;">
        <li>Click <strong>Submit Candidates</strong> to upload and process the resumes.</li>
      </ul>
    </div>
    <div style="background:#f0fdf4;border-left:3px solid #22c55e;padding:14px 16px;border-radius:0 8px 8px 0;margin-bottom:24px;">
      <p style="margin:0;font-size:13px;color:#166534;line-height:1.6;">That's it! You will receive an email notification once the resumes have been uploaded and processed successfully.</p>
    </div>
    <div style="margin-bottom:28px;">
      <div style="background:#eff6ff;border-left:3px solid #0f52ba;padding:14px 16px;border-radius:0 8px 8px 0;">
        <p style="margin:0 0 8px;font-size:13px;font-weight:700;color:#0f52ba;">📧 Step 4 — Send Profiles to the Client</p>
        <p style="margin:0 0 10px;font-size:13px;color:#1e3a5f;line-height:1.6;">After your uploads succeed, use the built-in <strong>Send Profiles</strong> feature on the portal — no manual copy-pasting needed:</p>
        <ol style="margin:0;padding-left:18px;font-size:13px;color:#334155;line-height:1.9;">
          <li>Click the <strong>📧 Send</strong> button on the left side of the portal.</li>
          <li>Select the candidates you want to share (today's uploads load by default).</li>
          <li>Fill in the candidate details in the highlighted columns (experience, billing rate, location, etc.).</li>
          <li>Review the <strong>To</strong>, <strong>Subject</strong>, and <strong>Body</strong> — then click <strong>Send Email</strong>.</li>
        </ol>
        <p style="margin:10px 0 0;font-size:12px;color:#475569;">The portal automatically formats the candidate tracker table, attaches the resumes, and sends with your signature.</p>
      </div>
    </div>
    <hr style="border:none;border-top:1px solid #e2e8f0;margin:0 0 24px;">
    <p style="color:#334155;font-size:14px;margin:0 0 16px;">To activate your account, click the button below:</p>
    <p style="margin:0 0 16px;">
      <a href="${confirmationUrl}" style="display:inline-block;padding:12px 28px;background:#0f52ba;color:#ffffff;text-decoration:none;border-radius:6px;font-weight:600;font-size:15px;">Activate My Account</a>
    </p>
    <p style="color:#94a3b8;font-size:12px;margin:0 0 24px;">If the button doesn't work, copy and paste this link:<br/><a href="${confirmationUrl}" style="color:#0f52ba;">${confirmationUrl}</a></p>
    <p style="color:#64748b;font-size:13px;margin:0;">Please feel free to reach out if you have any questions.</p>
  </div>
  <div style="background:#f8fafc;padding:16px 32px;text-align:center;border-top:1px solid #e2e8f0;">
    <p style="color:#94a3b8;font-size:12px;margin:0;">VOLIBITS · Resume Upload Portal · resupd.volibits.com</p>
  </div>
</div></div>`;
}

function otpHtml(token: string): string {
  return `
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f1f5f9;margin:0;padding:24px;">
<div style="max-width:560px;margin:0 auto;background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.1);">
  <div style="background:#0f52ba;padding:28px 32px;text-align:center;">
    <div style="font-size:28px;margin-bottom:8px;">📤</div>
    <h1 style="color:#fff;font-size:20px;font-weight:700;margin:0;">Resume Upload Portal</h1>
    <p style="color:rgba(255,255,255,.75);font-size:13px;margin:6px 0 0;">Client Candidate Submission · VOLIBITS</p>
  </div>
  <div style="padding:32px;">
    <p style="color:#334155;font-size:15px;margin:0 0 6px;">You've been granted access to the Resume Upload Portal.</p>
    <p style="color:#64748b;font-size:14px;margin:0 0 24px;">Use the code below to log in:</p>
    <div style="text-align:center;background:#f8fafc;border:2px dashed #cbd5e1;border-radius:10px;padding:22px;margin-bottom:28px;">
      <div style="font-size:11px;text-transform:uppercase;letter-spacing:1px;color:#94a3b8;margin-bottom:10px;">Your one-time access code</div>
      <div style="font-size:40px;font-weight:800;letter-spacing:12px;color:#0f52ba;font-family:monospace;">${token}</div>
      <div style="font-size:12px;color:#94a3b8;margin-top:10px;">Expires in 60 minutes</div>
    </div>
    <p style="color:#64748b;font-size:13px;margin:0 0 8px;">Enter this code on the login page where it says <em>"Enter 6-digit code"</em> and click <strong>Verify</strong>.</p>
    <p style="color:#94a3b8;font-size:12px;margin:0;">If you did not request this code, you can safely ignore this email.</p>
  </div>
  <div style="background:#f8fafc;padding:16px 32px;text-align:center;border-top:1px solid #e2e8f0;">
    <p style="color:#94a3b8;font-size:12px;margin:0;">VOLIBITS · Resume Upload Portal · resupd.volibits.com</p>
  </div>
</div></div>`;
}

function chatNotifyHtml(fromEmail: string, message: string): string {
  return `<div style="font-family:Inter,Arial,sans-serif;max-width:560px;margin:0 auto;border-radius:12px;overflow:hidden;border:1px solid #e2e8f0;">
  <div style="background:linear-gradient(135deg,#0f52ba,#1a6fd4);padding:28px 32px;text-align:center;">
    <div style="font-size:32px;margin-bottom:8px;">💬</div>
    <h1 style="color:#fff;font-size:20px;font-weight:700;margin:0;">New Support Message</h1>
    <p style="color:rgba(255,255,255,.8);font-size:13px;margin:6px 0 0;">Resume Upload Portal · VOLIBITS</p>
  </div>
  <div style="padding:28px 32px;background:#fff;">
    <p style="color:#334155;font-size:14px;margin:0 0 16px;">A user has sent a new message in the support chat:</p>
    <div style="background:#f1f5f9;border-left:4px solid #0f52ba;border-radius:6px;padding:14px 16px;margin-bottom:20px;">
      <p style="color:#1e293b;font-size:13px;font-weight:600;margin:0 0 4px;">${fromEmail}</p>
      <p style="color:#334155;font-size:14px;margin:0;line-height:1.5;">${message.replace(/</g,"&lt;").replace(/>/g,"&gt;")}</p>
    </div>
    <a href="http://resupd.volibits.com" style="display:inline-block;background:#0f52ba;color:#fff;text-decoration:none;padding:11px 24px;border-radius:8px;font-size:14px;font-weight:600;">Open Portal to Reply</a>
  </div>
  <div style="background:#f8fafc;padding:14px 32px;text-align:center;border-top:1px solid #e2e8f0;">
    <p style="color:#94a3b8;font-size:12px;margin:0;">VOLIBITS · Resume Upload Portal · resupd.volibits.com</p>
  </div>
</div>`;
}

async function notifyTeams(fromEmail: string, message: string): Promise<{ status: number; body: string } | null> {
  if (!TEAMS_WEBHOOK) return null;
  try {
    const res  = await fetch(TEAMS_WEBHOOK, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        type: "message",
        attachments: [{
          contentType: "application/vnd.microsoft.card.adaptive",
          content: {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            type: "AdaptiveCard",
            version: "1.4",
            body: [
              { type: "TextBlock", text: "💬 New Support Message", weight: "Bolder", size: "Medium", color: "Accent" },
              { type: "TextBlock", text: `**From:** ${fromEmail}`, wrap: true },
              { type: "TextBlock", text: message, wrap: true },
              { type: "TextBlock", text: "Resume Upload Portal · VOLIBITS", size: "Small", isSubtle: true },
            ],
            actions: [{
              type: "Action.OpenUrl",
              title: "Open Portal to Reply",
              url: "http://resupd.volibits.com",
            }],
          },
        }],
      }),
    });
    const body = await res.text().catch(() => "");
    console.log("Teams response:", res.status, body);
    return { status: res.status, body };
  } catch (err) {
    console.error("Teams notify failed:", err);
    return { status: 0, body: String(err) };
  }
}

serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: CORS_HEADERS });
  }

  try {
    const body = await req.json();

    let to: string | string[] = "";
    let subject = "";
    let html    = "";
    let cc: string[] = [];

    if (body.type === "invite") {
      // Direct call from portal admin panel — send welcome/invite email
      to      = body.email;
      subject = "[Volibits]: You're invited to the Resume Upload Portal";
      html    = inviteHtml("http://resupd.volibits.com/");
      cc      = Array.isArray(body.cc) ? body.cc.filter((e: string) => e !== to) : [];
    } else if (body.type === "otp") {
      // Direct OTP call from portal login — custom code
      to      = body.email;
      subject = "[Volibits]: Your Resume Upload Portal login code";
      html    = otpHtml(body.code);
    } else if (body.type === "client-email") {
      // Outbound candidate profiles email.
      // Volibits users → send from their own mailbox.
      // External users → send from the bot mailbox (Graph app only has access to Volibits tenant).
      to      = Array.isArray(body.to) ? body.to : [body.to];
      const toSet = new Set((to as string[]).map((e: string) => e.toLowerCase()));
      cc      = Array.isArray(body.cc) ? body.cc.filter((e: string) => !toSet.has(e.toLowerCase())) : [];
      subject = body.subject;
      html    = body.html_body;
      const senderDomain    = (body.sender_email || "").split("@")[1]?.toLowerCase() ?? "";
      const isInternal      = senderDomain === "volibits.com";
      const effectiveSender = isInternal ? body.sender_email : SENDER_EMAIL;
      const senderName      = isInternal ? undefined : (body.sender_name || undefined);
      const replyTo         = isInternal ? undefined : (body.sender_email || undefined);
      const fileAttachments: Attachment[] = Array.isArray(body.attachments)
        ? body.attachments.filter((a: Attachment) => a.name && a.contentBytes)
        : [];
      const azureToken = await getAzureToken();
      await sendEmail(azureToken, to, subject, html, cc, effectiveSender, fileAttachments, replyTo, senderName);
      return new Response(JSON.stringify({ success: true }), {
        headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
      });
    } else if (body.type === "chat-notify") {
      // Chat message notification to admins
      const admins: string[] = Array.isArray(body.admin_emails) ? body.admin_emails : [];
      if (!admins.length) throw new Error("No admin_emails provided");
      to      = admins[0];
      cc      = admins.slice(1);
      subject = `[Volibits Portal] New support message from ${body.from_email}`;
      html    = chatNotifyHtml(body.from_email, body.message);
    } else {
      // Auth Hook fallback (legacy)
      const { user, email_data } = body;
      to      = user.email;
      subject = "[Volibits]: Your Resume Upload Portal login code";
      html    = otpHtml(email_data.token);
    }

    const azureToken = await getAzureToken();
    await sendEmail(azureToken, to, subject, html, cc);

    const extra = (body?.type === "chat-notify") ? { teams: (await notifyTeams(body.from_email, body.message)) } : {};
    return new Response(JSON.stringify({ success: true, ...extra }), {
      headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
    });
  } catch (err) {
    console.error(err);
    return new Response(JSON.stringify({ error: (err as Error).message }), {
      status: 500,
      headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
    });
  }
});
