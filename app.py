from flask import Flask
import imaplib
import email
import os
import groq
import requests
from fpdf import FPDF
import tempfile
import datetime
from email.utils import parsedate_to_datetime

app = Flask(__name__)

# Telegram functions
def send_telegram_message(message):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {"chat_id": chat_id, "text": message}
    requests.post(url, data=data)

def send_telegram_pdf(file_path):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    files = {"document": open(file_path, "rb")}
    data = {"chat_id": chat_id}
    requests.post(url, files=files, data=data)

# PDF creation
import unicodedata

def clean_text(text):
    return unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')

def generate_pdf(subject, sender, body_text):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)

    subject = clean_text(subject)
    sender = clean_text(sender)
    body_text = clean_text(body_text)

    pdf.multi_cell(0, 10, f"Subject: {subject}")
    pdf.multi_cell(0, 10, f"From: {sender}")
    pdf.multi_cell(0, 10, f"\n{body_text}")

    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    pdf.output(temp_file.name)
    return temp_file.name


@app.route("/check-email", methods=["GET"])
def check_email():
    try:
        # Connect to email
        mail = imaplib.IMAP4_SSL("imap.gmail.com")  # Change if not Gmail
        mail.login(os.environ["EMAIL_USER"], os.environ["EMAIL_PASS"])
        mail.select("inbox")

        # Get the date 5 minutes ago in the format needed for IMAP
        # Make it timezone aware by adding UTC timezone
        five_min_ago = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)
        date_str = five_min_ago.strftime("%d-%b-%Y")
        
        # Search for unseen messages from the last 5 minutes
        result, data = mail.search(None, f'(UNSEEN SENTSINCE {date_str})')
        email_ids = data[0].split()
        
        # Further filter by exact time (IMAP search only supports date precision)
        filtered_ids = []
        for eid in email_ids:
            result, data = mail.fetch(eid, "(INTERNALDATE)")
            date_str = data[0].decode('utf-8')
            # Extract the date and time information
            import re
            match = re.search(r'INTERNALDATE "([^"]+)"', date_str)
            if match:
                date_time_str = match.group(1)
                # parsedate_to_datetime returns timezone-aware datetime
                email_date = email.utils.parsedate_to_datetime(date_time_str)
                # Both email_date and five_min_ago are now timezone-aware
                if email_date > five_min_ago:
                    filtered_ids.append(eid)
        
        email_ids = filtered_ids

        if not email_ids:
            return "No new emails in the last 5 minutes"

        # Initialize Groq client
        client = groq.Groq(api_key=os.environ["GROQ_API_KEY"])

        for eid in email_ids:
            result, data = mail.fetch(eid, "(RFC822)")
            raw = data[0][1]
            msg = email.message_from_bytes(raw)

            subject = msg["subject"]
            sender = msg["from"]

            # Extract plain text body
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
                        body += part.get_payload(decode=True).decode(errors="ignore")
            else:
                body = msg.get_payload(decode=True).decode(errors="ignore")

            # Send to LLM using Groq
            prompt = f"""You're an AI recruitment mail filter for a B. Tech Computer Science student with a CGPA of 8.26. For the given email, answer:

1. Is this email about job or internship opportunities?
2. Is there a CGPA requirement mentioned? If yes, is 8.26 eligible?
3. Is it specifically for B. Tech CSE students?

If all answers are YES, output: "YES"
Otherwise, say "NO"
.

Subject: {subject}
From: {sender}

{body}"""

            # Groq API call
            response = client.chat.completions.create(
                model="llama3-8b-8192",  # or another model like "mixtral-8x7b-32768"
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": prompt}
                ]
            )

            decision = response.choices[0].message.content.strip().lower()

            if "yes" in decision:
                # Generate and send PDF
                pdf_path = generate_pdf(subject, sender, body)
                send_telegram_message(f"📬 Relevant email received!\nSubject: {subject}\nFrom: {sender}")
                send_telegram_pdf(pdf_path)
            else:
                send_telegram_message(f"🔍 Irrelevant email skipped: {subject}")

        return f"Processed {len(email_ids)} email(s) from the last 5 minutes"

    except Exception as e:
        return f"❌ Error: {str(e)}", 500
        
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))  # Render sets PORT env var
    app.run(host="0.0.0.0", port=port)
