import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr


def send_smtp_message(
    *,
    host: str,
    port: int,
    security: str,
    username: str,
    password: str,
    from_email: str,
    from_name: str,
    recipient: str,
    subject: str,
    body: str,
) -> None:
    message = EmailMessage()
    message["From"] = formataddr((from_name, from_email))
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    context = ssl.create_default_context()
    client = (
        smtplib.SMTP_SSL(host, port, timeout=5, context=context)
        if security == "tls"
        else smtplib.SMTP(host, port, timeout=5)
    )
    with client:
        if security == "starttls":
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        if username:
            client.login(username, password)
        refused = client.send_message(message)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
