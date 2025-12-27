#!/usr/bin/env python3
import smtplib
import os
import json
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse as urlparse

# Configuration
CONFIG_FILE = "smtp_config.json"
USER_FILE = "users.json"
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

class SMTPRiverHandler(BaseHTTPRequestHandler):
    
    def _set_headers(self, content_type='text/html; charset=utf-8'):
        self.send_response(200)
        self.send_header('Content-type', content_type)
        self.end_headers()
    
    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            pass
    
    def do_GET(self):
        try:
            if self.path == '/':
                if not self.check_auth():
                    self.send_login_page()
                    return
                self.send_main_page()
            elif self.path == '/login':
                self.send_login_page()
            elif self.path == '/logout':
                # Clear auth and redirect to login page
                self.send_response(302)
                self.send_header('Location', '/login')
                self.send_header('Set-Cookie', 'authenticated=false; expires=Thu, 01 Jan 1970 00:00:00 GMT')
                self.end_headers()
                return
            else:
                self.send_error(404, "File not found")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        try:
            if self.path == '/login':
                content_length = int(self.headers.get('Content-Length', 0))
                post_data = self.rfile.read(content_length)
                post_data = urlparse.parse_qs(post_data.decode('utf-8'))
                
                username = post_data.get('username', [''])[0]
                password = post_data.get('password', [''])[0]
                
                if self.authenticate(username, password):
                    self.set_auth(username)
                    self.send_main_page()
                else:
                    self.send_login_page(error="Invalid login")
                    
            elif self.path == '/send_message':
                if not self.check_auth():
                    self.send_login_page()
                    return
                    
                content_length = int(self.headers.get('Content-Length', 0))
                post_data = self.rfile.read(content_length)
                
                # Parse multipart form data for images
                content_type = self.headers.get('Content-Type', '')
                if 'multipart/form-data' in content_type:
                    result = self.handle_multipart_form(post_data, content_type)
                else:
                    post_data = urlparse.parse_qs(post_data.decode('utf-8'))
                    result = self.send_email_simple(post_data)
                
                self.send_main_page(result=result)
                
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            self.send_main_page(result=f"Error: {str(e)}")
    
    def handle_multipart_form(self, post_data, content_type):
        try:
            boundary = content_type.split('boundary=')[1].encode()
            parts = post_data.split(b'--' + boundary)
            
            form_data = {}
            image_data = None
            image_filename = None
            
            for part in parts:
                if b'name="' in part:
                    headers, body = part.split(b'\r\n\r\n', 1)
                    headers = headers.decode('utf-8')
                    
                    if 'name="' in headers:
                        field_name = headers.split('name="')[1].split('"')[0]
                        
                        if 'filename="' in headers:
                            # File upload
                            image_filename = headers.split('filename="')[1].split('"')[0]
                            image_data = body[:-2]
                        else:
                            # Form field
                            form_data[field_name] = body[:-2].decode('utf-8')
            
            return self.send_email_with_image(
                form_data.get('recipient', ''),
                form_data.get('subject', ''),
                form_data.get('message', ''),
                form_data.get('sender_name', ''),
                image_data,
                image_filename
            )
            
        except Exception as e:
            return f"Error: {str(e)}"
    
    def send_email_simple(self, post_data):
        recipient = post_data.get('recipient', [''])[0]
        subject = post_data.get('subject', [''])[0]
        message = post_data.get('message', [''])[0]
        sender_name = post_data.get('sender_name', [''])[0]
        return self.send_email_with_image(recipient, subject, message, sender_name, None, None)
    
    def send_email_with_image(self, recipient, subject, message, sender_name, image_data, image_filename):
        try:
            config = self.load_config()
            
            if not config.get('your_email') or not config.get('app_password'):
                return "Configure email in smtp_config.json"
            
            msg = MIMEMultipart()
            msg['From'] = f'{sender_name} <{config["your_email"]}>'
            msg['To'] = recipient
            msg['Subject'] = subject
            
            # Create HTML message with image AT THE TOP
            html_message = f"""
            <html>
                <body>
                    <div style="font-family: Arial, sans-serif; max-width: 100%;">
                        <div style="background: #f8f9fa; padding: 15px; border-radius: 8px;">
                            <h2 style="color: #333; margin: 0 0 15px 0;">{subject}</h2>
            """
            
            # IMAGE AT THE TOP - before the message
            if image_data and image_filename:
                # Save and attach image
                image_path = os.path.join(UPLOAD_FOLDER, image_filename)
                with open(image_path, 'wb') as f:
                    f.write(image_data)
                
                with open(image_path, 'rb') as f:
                    img = MIMEImage(f.read())
                    img.add_header('Content-ID', '<image1>')
                    img.add_header('Content-Disposition', 'inline', filename=image_filename)
                    msg.attach(img)
                
                html_message += f"""
                            <div style="margin-bottom: 15px; text-align: center;">
                                <img src="cid:image1" style="max-width: 100%; max-height: 400px; border-radius: 6px; border: 1px solid #ddd;">
                            </div>
                """
                
                os.remove(image_path)
            
            # MESSAGE BELOW THE IMAGE
            html_message += f"""
                            <div style="white-space: pre-line; line-height: 1.5; color: #555; padding: 10px 0;">
                                {message}
                            </div>
                        </div>
                    </div>
                </body>
            </html>
            """
            
            # ONLY attach HTML version to avoid duplicates
            msg.attach(MIMEText(html_message, 'html'))
            
            server = smtplib.SMTP(config['smtp_server'], config['smtp_port'])
            server.starttls()
            server.login(config['your_email'], config['app_password'])
            text = msg.as_string()
            server.sendmail(config['your_email'], recipient, text)
            server.quit()
            
            if image_data:
                return f"Sent with image to {recipient}"
            else:
                return f"Sent to {recipient}"
            
        except smtplib.SMTPAuthenticationError:
            return "Auth failed - check password"
        except Exception as e:
            return f"Error: {str(e)}"
    
    def check_auth(self):
        cookie = self.headers.get('Cookie', '')
        return 'authenticated=true' in cookie
    
    def set_auth(self, username):
        self.send_response(200)
        self.send_header('Set-Cookie', f'authenticated=true; username={username}')
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
    
    def clear_auth(self):
        self.send_response(302)
        self.send_header('Location', '/login')
        self.send_header('Set-Cookie', 'authenticated=false; expires=Thu, 01 Jan 1970 00:00:00 GMT')
        self.end_headers()
    
    def authenticate(self, username, password):
        users = self.load_users()
        return username in users and users[username]['password'] == password
    
    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        return {"smtp_server": "smtp.gmail.com", "smtp_port": 587, "your_email": "", "app_password": ""}
    
    def load_users(self):
        if os.path.exists(USER_FILE):
            with open(USER_FILE, 'r') as f:
                return json.load(f)
        return {"admin": {"password": "admin123", "email": "admin@localhost"}}
    
    def send_login_page(self, error=None):
        self._set_headers()
        
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
            <title>SMTP River</title>
            <style>
                * { box-sizing: border-box; margin: 0; padding: 0; }
                body { 
                    font-family: -apple-system, BlinkMacSystemFont, sans-serif;
                    background: #f0f2f5;
                    padding: 20px;
                    min-height: 100vh;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                }
                .login-box {
                    background: white;
                    padding: 30px 25px;
                    border-radius: 12px;
                    box-shadow: 0 4px 12px rgba(0,0,0,0.1);
                    width: 100%;
                    max-width: 400px;
                }
                h2 {
                    text-align: center;
                    color: #1a1a1a;
                    margin-bottom: 25px;
                    font-size: 24px;
                }
                input {
                    width: 100%;
                    padding: 14px;
                    margin: 8px 0;
                    border: 1px solid #ddd;
                    border-radius: 8px;
                    font-size: 16px;
                    background: #fafafa;
                }
                button {
                    width: 100%;
                    background: #007cba;
                    color: white;
                    padding: 16px;
                    border: none;
                    border-radius: 8px;
                    font-size: 17px;
                    font-weight: 600;
                    margin-top: 10px;
                    cursor: pointer;
                }
                .error {
                    color: #d32f2f;
                    background: #ffebee;
                    padding: 12px;
                    border-radius: 6px;
                    margin-bottom: 15px;
                    text-align: center;
                    font-size: 14px;
                }
                .info {
                    text-align: center;
                    margin-top: 20px;
                    color: #666;
                    font-size: 14px;
                }
            </style>
        </head>
        <body>
            <div class="login-box">
                <h2>SMTP River</h2>
        """
        
        if error:
            html += f'<div class="error">{error}</div>'
            
        html += """
                <form method="POST" action="/login">
                    <input type="text" name="username" placeholder="Username" value="admin" required>
                    <input type="password" name="password" placeholder="Password" value="admin123" required>
                    <button type="submit">Login</button>
                </form>
                <div class="info">Default: admin / admin123</div>
            </div>
        </body>
        </html>
        """
        
        try:
            self.wfile.write(html.encode('utf-8'))
        except (BrokenPipeError, ConnectionResetError):
            pass
    
    def send_main_page(self, result=None):
        self._set_headers()
        
        config = self.load_config()
        email_status = "Ready" if config.get('your_email') and config.get('app_password') else "Not configured"
        
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
            <title>SMTP River</title>
            <style>
                * { box-sizing: border-box; margin: 0; padding: 0; }
                body {
                    font-family: -apple-system, BlinkMacSystemFont, sans-serif;
                    background: #f0f2f5;
                    padding: 15px;
                    line-height: 1.4;
                }
                .container {
                    max-width: 100%;
                }
                .header {
                    background: white;
                    padding: 20px;
                    border-radius: 12px;
                    margin-bottom: 15px;
                    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                }
                .message-form {
                    background: white;
                    padding: 20px;
                    border-radius: 12px;
                    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                    margin-bottom: 15px;
                }
                input, textarea {
                    width: 100%;
                    padding: 14px;
                    margin: 8px 0;
                    border: 1px solid #ddd;
                    border-radius: 8px;
                    font-size: 16px;
                    background: #fafafa;
                }
                textarea {
                    height: 120px;
                    resize: vertical;
                }
                .send-btn {
                    width: 100%;
                    background: #007cba;
                    color: white;
                    padding: 16px;
                    border: none;
                    border-radius: 8px;
                    font-size: 17px;
                    font-weight: 600;
                    margin-top: 10px;
                    cursor: pointer;
                }
                .send-btn:disabled {
                    background: #ccc;
                    cursor: not-allowed;
                }
                .result {
                    margin: 12px 0;
                    padding: 14px;
                    border-radius: 8px;
                    background: #e8f5e8;
                    border-left: 4px solid #4caf50;
                    font-size: 14px;
                }
                .result.error {
                    background: #ffebee;
                    border-left-color: #f44336;
                }
                .logout {
                    float: right;
                    color: #007cba;
                    text-decoration: none;
                    padding: 8px 16px;
                    border: 1px solid #007cba;
                    border-radius: 6px;
                    font-size: 14px;
                }
                .status {
                    margin: 12px 0;
                    padding: 12px;
                    background: #f8f9fa;
                    border-radius: 6px;
                    font-size: 14px;
                }
                .photo-section {
                    margin: 15px 0;
                    padding: 15px;
                    background: #f8f9fa;
                    border-radius: 8px;
                    border: 2px dashed #ddd;
                }
                .file-input {
                    display: none;
                }
                .file-label {
                    display: block;
                    text-align: center;
                    padding: 14px;
                    background: #007cba;
                    color: white;
                    border-radius: 8px;
                    font-size: 16px;
                    font-weight: 500;
                    cursor: pointer;
                    margin-bottom: 10px;
                }
                .preview-container {
                    text-align: center;
                    margin-top: 10px;
                }
                .image-preview {
                    max-width: 200px;
                    max-height: 200px;
                    border-radius: 6px;
                    border: 1px solid #ddd;
                    display: none;
                }
                .loading {
                    display: none;
                    text-align: center;
                    margin: 15px 0;
                }
                .spinner {
                    border: 3px solid #f3f3f3;
                    border-top: 3px solid #007cba;
                    border-radius: 50%;
                    width: 30px;
                    height: 30px;
                    animation: spin 1s linear infinite;
                    margin: 0 auto 10px;
                }
                @keyframes spin {
                    0% { transform: rotate(0deg); }
                    100% { transform: rotate(360deg); }
                }
                h1 {
                    font-size: 22px;
                    color: #1a1a1a;
                    margin-bottom: 10px;
                }
                h2 {
                    font-size: 18px;
                    color: #333;
                    margin-bottom: 15px;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>SMTP River</h1>
                    <a href="/logout" class="logout">Logout</a>
                    <div class="status">
                        <strong>Status:</strong> """ + email_status + """
                    </div>
        """
        
        if result:
            is_error = any(word in result.lower() for word in ['error', 'failed', 'invalid'])
            html += f'<div class="result{" error" if is_error else ""}">{result}</div>'
            
        html += """
                </div>
                
                <div class="message-form">
                    <h2>Send Message</h2>
                    <form method="POST" action="/send_message" enctype="multipart/form-data" id="messageForm">
                        <input type="text" name="sender_name" placeholder="Your Name" required>
                        <input type="email" name="recipient" placeholder="To Email" required>
                        <input type="text" name="subject" placeholder="Subject" required>
                        <textarea name="message" placeholder="Type your message here..." required></textarea>
                        
                        <div class="photo-section">
                            <h3 style="margin-bottom: 12px; font-size: 16px;">Add Photo (Optional)</h3>
                            <input type="file" name="photo" id="photo" accept="image/*" class="file-input">
                            <label for="photo" class="file-label">📷 Choose Photo</label>
                            <div class="preview-container">
                                <img id="imagePreview" class="image-preview" alt="Preview">
                            </div>
                        </div>
                        
                        <div class="loading" id="loading">
                            <div class="spinner"></div>
                            <div>Sending...</div>
                        </div>
                        
                        <button type="submit" class="send-btn" id="sendBtn">
                            Send Message
                        </button>
                    </form>
                </div>
            </div>

            <script>
                // Image preview
                document.getElementById('photo').addEventListener('change', function(e) {
                    const file = e.target.files[0];
                    const preview = document.getElementById('imagePreview');
                    
                    if (file) {
                        const reader = new FileReader();
                        reader.onload = function(e) {
                            preview.src = e.target.result;
                            preview.style.display = 'block';
                        };
                        reader.readAsDataURL(file);
                    } else {
                        preview.style.display = 'none';
                    }
                });
                
                // Loading animation
                document.getElementById('messageForm').addEventListener('submit', function() {
                    document.getElementById('loading').style.display = 'block';
                    document.getElementById('sendBtn').disabled = true;
                    document.getElementById('sendBtn').textContent = 'Sending...';
                });
            </script>
        </body>
        </html>
        """
        
        try:
            self.wfile.write(html.encode('utf-8'))
        except (BrokenPipeError, ConnectionResetError):
            pass

def run_server():
    port = 8080
    server = HTTPServer(('0.0.0.0', port), SMTPRiverHandler)
    print(f"SMTP River running on http://localhost:{port}")
    print("No duplicate messages - fixed!")
    print("Images at top of email")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped")

if __name__ == '__main__':
    run_server()
