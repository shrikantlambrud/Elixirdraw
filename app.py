from flask import (
    Flask, render_template, request,
    redirect, session, url_for, flash, g
)

from flask_mail import Mail, Message

import mysql.connector
import random
import os
import time

from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import re
import os
import traceback
app = Flask(__name__)
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")

# 🌐 Public site domain (used for links / canonical URLs).
# NOTE: changing this constant does NOT move your hosting. To actually make
# www.elixirdraw.com serve this app you must (1) point the domain's DNS/CNAME
# at your host, and (2) add the custom domain in your hosting provider's
# dashboard (e.g. PythonAnywhere → Web tab → Add a new web app / custom domain).
SITE_DOMAIN = "https://www.elixirdraw.com"


# DATABASE CONNECTION
# IMPORTANT FIX: previously every get_db() call opened a brand new MySQL
# connection, and ~20+ routes across this file never closed it. PythonAnywhere's
# MySQL has a hard cap on simultaneous connections — once that cap is hit,
# EVERY query on EVERY route (wallet, offers, materials, etc.) starts failing
# with "Too many connections", which surfaces to users as a generic Internal
# Server Error. This exactly matches "works at first, then fails everywhere".
#
# Fix: reuse ONE connection per request (stored on Flask's `g`), and let
# Flask close it automatically when the request ends. Any existing explicit
# db.close() calls elsewhere are harmless no-ops now.

def _db_config():
    """DB settings come from environment variables (set them in Render ->
    Environment). Works with any MySQL host: Aiven, TiDB Cloud, Railway,
    Hostinger remote MySQL, or a self-hosted MySQL."""
    cfg = dict(
        host=(os.environ.get("DB_HOST") or "mysql-32feb24f-elixirdraw26-af24.b.aivencloud.com").strip(),
        port=int(os.environ.get("DB_PORT", "13179")),
        user=(os.environ.get("DB_USER") or "avnadmin").strip(),
        password=os.environ.get("DB_PASSWORD", "AVNS__aJ7h2qPd_hyF1PlJ3q"),
        database=(os.environ.get("DB_NAME") or "defaultdb").strip(),
        connection_timeout=15,
    )
    ssl_ca = os.environ.get("DB_SSL_CA")          # path to CA file, if provider needs it
    if ssl_ca:
        cfg["ssl_ca"] = ssl_ca
    elif os.environ.get("DB_SSL", "").lower() in ("1", "true", "yes"):
        cfg["ssl_disabled"] = False
    return cfg

def get_db():
    if 'db' not in g or not g.db.is_connected():
        g.db = mysql.connector.connect(**_db_config())
    return g.db


@app.teardown_appcontext
def close_db_connection(exception=None):
    db = g.pop('db', None)
    if db is not None:
        try:
            db.close()
        except Exception:
            pass


# ==============================
# 🔹 ONE-TIME STARTUP MIGRATIONS
# ==============================
# Adds columns/tables required by the new features if they don't already
# exist. Safe to leave in place — it only runs once, at process startup,
# instead of on every request (the old code re-ran an ALTER TABLE on every
# single status-change click, which was slow and could fail on older MySQL).
def _column_exists(cur, table, column):
    cur.execute("""
        SELECT COUNT(*) AS cnt FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s
    """, (table, column))
    result = cur.fetchone()
    # Works with BOTH dictionary=True cursors and plain cursors — some
    # callers pass one, some pass the other, and a mismatch here previously
    # crashed with KeyError and left the cursor in a bad "unread result"
    # state that broke every query after it in the same request.
    if isinstance(result, dict):
        return (result.get("cnt") or 0) > 0
    return (result[0] if result else 0) > 0


def _ensure_column(cur, table, column, ddl):
    """Add `column` to `table` using `ddl` only if it doesn't already exist.
    Works on ALL MySQL/MariaDB versions (doesn't rely on ADD COLUMN IF NOT EXISTS,
    which only MySQL 8.0.29+ supports and silently no-ops on older servers)."""
    if not _column_exists(cur, table, column):
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def run_migrations():
    try:
        db = mysql.connector.connect(**_db_config())
    except Exception as e:
        print("MIGRATION ERROR: could not connect to DB at startup:", e)
        return

    cur = db.cursor()

    # IMPORTANT FIX: each column now gets its OWN try/except. Previously all
    # of these ran inside a single try block — if any ONE statement failed
    # (e.g. "materials" table not existing yet), every statement AFTER it in
    # the list was silently skipped, permanently. That's exactly how
    # users.plan_name / plan_expiry (needed by Offers) and materials.category
    # (needed by Materials) could end up missing even after "deploying" this
    # migration, causing "Unknown column" errors -> Internal Server Error.
    column_migrations = [
        ("properties", "status", "VARCHAR(20) DEFAULT 'active'"),
        ("properties", "is_top_listing", "TINYINT(1) DEFAULT 0"),
        ("properties", "contact_number", "VARCHAR(20) DEFAULT NULL"),
        ("properties", "listing_type", "VARCHAR(10) DEFAULT 'sell'"),
        ("properties", "locality", "VARCHAR(100) DEFAULT NULL"),
        ("properties", "area_value", "VARCHAR(20) DEFAULT NULL"),
        ("properties", "area_unit", "VARCHAR(20) DEFAULT NULL"),
        ("properties", "full_address", "VARCHAR(255) DEFAULT NULL"),
        ("materials", "category", "VARCHAR(30) DEFAULT NULL"),
        ("users", "plan_name", "VARCHAR(20) DEFAULT NULL"),
        ("users", "plan_expiry", "DATETIME DEFAULT NULL"),
        ("users", "sell_listings_left", "INT DEFAULT 0"),
        ("users", "rent_listings_left", "INT DEFAULT 0"),
        ("users", "top_listings_left", "INT DEFAULT 0"),
        ("users", "free_offer_entries", "INT DEFAULT 0"),
    ]

    for table, column, ddl in column_migrations:
        try:
            _ensure_column(cur, table, column, ddl)
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"MIGRATION SKIP {table}.{column}:", e)

    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS service_providers (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                category VARCHAR(50) NOT NULL,
                experience VARCHAR(20) NOT NULL,
                about TEXT,
                id_doc1_type VARCHAR(50),
                id_doc1 VARCHAR(255),
                id_doc2_type VARCHAR(50),
                id_doc2 VARCHAR(255),
                professional_doc_type VARCHAR(80),
                professional_doc VARCHAR(255),
                status VARCHAR(20) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.commit()
    except Exception as e:
        db.rollback()
        print("MIGRATION SKIP service_providers table:", e)

    for column, ddl in [
        ("id_doc1_type", "VARCHAR(50)"),
        ("id_doc2_type", "VARCHAR(50)"),
        ("professional_doc_type", "VARCHAR(80)"),
    ]:
        try:
            _ensure_column(cur, "service_providers", column, ddl)
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"MIGRATION SKIP service_providers.{column}:", e)

    cur.close()
    db.close()
    print("MIGRATIONS DONE")

try:
    run_migrations()
except Exception as _e:
    print("MIGRATION FAILED (app will still start):", _e)

# ---------------- MAIL CONFIG ----------------

app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', 'elixirdraw26@gmail.com')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', '')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_USERNAME', 'elixirdraw26@gmail.com')
mail = Mail(app)


# ==============================
# 📩 EMAIL SENDING (works on Render)
# ==============================
# Render blocks SMTP ports (25/465/587) on free web services, so Gmail SMTP
# fails and register / OTP crashes with Internal Server Error.
# If BREVO_API_KEY is set, emails go out over HTTPS (port 443), which is
# never blocked. Otherwise we fall back to Gmail SMTP. Either way a failure
# no longer crashes the page - the user just sees a message.
def send_mail(msg):
    import json
    import urllib.request

    api_key = os.environ.get("BREVO_API_KEY", "").strip()
    sender_email = os.environ.get("MAIL_FROM") or app.config['MAIL_USERNAME']

    try:
        if api_key:
            payload = {
                "sender": {"name": "ElixirDraw", "email": sender_email},
                "to": [{"email": r} for r in msg.recipients],
                "subject": msg.subject,
                "textContent": msg.body,
            }
            req = urllib.request.Request(
                "https://api.brevo.com/v3/smtp/email",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "api-key": api_key,
                    "content-type": "application/json",
                    "accept": "application/json",
                },
                method="POST",
            )
            urllib.request.urlopen(req, timeout=15).read()
        else:
            # SMTP fallback: short timeout so a blocked port can't freeze the worker
            import socket
            socket.setdefaulttimeout(10)
            mail.send(msg)
        return True
    except Exception as e:
        print("EMAIL SEND FAILED:", repr(e))
        try:
            body = e.read().decode() if hasattr(e, "read") else ""
            if body:
                print("EMAIL ERROR BODY:", body)
        except Exception:
            pass
        flash("Could not send the email right now. Please try again later.", "danger")
        return False

# # DATABASE CONNECTION
# def get_db():
#     return mysql.connector.connect(
#         host="elixirdraw.mysql.pythonanywhere-services.com",
#         user="elixirdraw",
#         database="elixirdraw$rental"
#     )

# # ---------------- MAIL CONFIG ----------------

# app.config['MAIL_SERVER'] = 'smtp.gmail.com'
# app.config['MAIL_PORT'] = 587
# app.config['MAIL_USE_TLS'] = True
# app.config['MAIL_USERNAME'] = 'elixirdraw@gmail.com'
# app.config['MAIL_DEFAULT_SENDER'] = 'elixirdraw@gmail.com'
# mail = Mail(app)

@app.route("/", methods=["GET"])
def index():
    # 🔍 Get search value
    search = request.args.get("search")

    # 🚫 If user tries search without login
    if search and not session.get("user_id"):
        return redirect("/login")

    # ✅ Only show homepage (no data fetch)
    return render_template("index.html")
# PERSONAL REGISTER


# UPLOAD_FOLDER = "static/uploads"
UPLOAD_FOLDER = "static/uploads"
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
# ==============================
# 🔹 REGISTER ROUTE
# ==============================
@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":
        name = request.form['name']
        email = request.form['email']
        phone = request.form['phone']
        address = request.form['address']

        # 🔐 Password Hash
        password = generate_password_hash(request.form['password'])

        # 📸 PROFILE PIC
        profile_pic = request.files.get("profile_pic")
        filename = ""

        if profile_pic and profile_pic.filename != "":
            os.makedirs(UPLOAD_FOLDER, exist_ok=True)

            safe_name = secure_filename(profile_pic.filename)
            filename = str(int(time.time())) + "_" + safe_name

            upload_path = os.path.join(UPLOAD_FOLDER, filename)
            profile_pic.save(upload_path)

        # 🔢 OTP
        otp = str(random.randint(100000, 999999))
        otp_expiry = datetime.now() + timedelta(minutes=10)

        # 💾 SESSION STORE
        session['temp_user'] = {
            "name": name,
            "email": email,
            "phone": phone,
            "address": address,
            "password": password,
            "profile_pic": filename,
            "otp": otp,
            "otp_expiry": otp_expiry.strftime("%Y-%m-%d %H:%M:%S")
        }

        # 📩 EMAIL SEND
        msg = Message(
            "Your OTP Verification - Personal Registration",
            sender=app.config['MAIL_USERNAME'],
            recipients=[email]
        )

        msg.body = f"""
Hello {name},

Thank you for registering with us.

To complete your registration, please use the OTP below:

----------------------------------------------------
Your OTP is: {otp}
----------------------------------------------------

⏳ This OTP is valid for 5 minutes only.

Please do not share this OTP with anyone.

If you did not request this registration, please ignore this email.

Best regards,
Support Team
ElixirDraw
"""

        send_mail(msg)

        flash("OTP sent successfully!", "success")
        return redirect(url_for("verify_otp"))

    return render_template("register.html")


# ==============================
# 🔹 VERIFY OTP ROUTE
# ==============================
@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():

    if request.method == "POST":
        user_otp = request.form['otp']
        temp_user = session.get('temp_user')

        if not temp_user:
            return "Session expired. Register again."

        # ⏱ OTP EXPIRY CHECK
        expiry_time = datetime.strptime(temp_user['otp_expiry'], "%Y-%m-%d %H:%M:%S")

        if datetime.now() > expiry_time:
            session.pop('temp_user', None)
            return "OTP expired. Please register again."

        # ❌ WRONG OTP
        if user_otp != temp_user.get('otp'):
            return "Invalid OTP"

        db = get_db()
        cursor = db.cursor()

        # 🔁 CHECK EMAIL
        cursor.execute("SELECT id FROM users WHERE email=%s", (temp_user['email'],))
        if cursor.fetchone():
            return "Email already exists"

        # ✅ INSERT USER
        cursor.execute("""
            INSERT INTO users 
            (name, email, phone, address, password, role, profile_pic)
            VALUES (%s,%s,%s,%s,%s,'personal',%s)
        """, (
            temp_user['name'],
            temp_user['email'],
            temp_user['phone'],
            temp_user['address'],
            temp_user['password'],
            temp_user['profile_pic']
        ))

        db.commit()

        # 🧹 CLEAR SESSION
        session.pop('temp_user', None)

        flash("Registration successful!", "success")
        return redirect("/login")

    return render_template("verify_otp.html")



# BUSINESS REGISTER
@app.route("/business_register", methods=["GET", "POST"])
def business_register():

    if request.method == "POST":

        business_name = request.form["business_name"]
        email = request.form["email"]
        phone = request.form["phone"]
        address = request.form["address"]
        udyam_no = request.form["udyam_no"]

        # 🔐 HASH PASSWORD
        password = generate_password_hash(request.form["password"])

        # 📁 FILE UPLOAD SAFE HANDLING
        file = request.files.get("document")
        filename = ""

        if file and file.filename != "":
            filename = secure_filename(file.filename)

            upload_path = os.path.join("static", "uploads")
            os.makedirs(upload_path, exist_ok=True)

            file.save(os.path.join(upload_path, filename))

        # 🔢 OTP GENERATE
        otp = str(random.randint(100000, 999999))

        # ⏱ OPTIONAL: OTP EXPIRY
        otp_expiry = (datetime.now() + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")

        # 💾 SESSION STORE
        session['temp_business'] = {
            "business_name": business_name,
            "email": email,
            "phone": phone,
            "address": address,
            "udyam_no": udyam_no,
            "password": password,
            "document": filename,
            "otp": otp,
            "otp_expiry": otp_expiry
        }

        # 📩 EMAIL OTP (PROFESSIONAL FORMAT)
        msg = Message(
            "Business Registration - OTP Verification",
            sender=app.config['MAIL_USERNAME'],
            recipients=[email]
        )

        msg.body = f"""
Hello {business_name},

Thank you for registering your business with us.

To complete your registration, please use the OTP below:

----------------------------------------------------
Your OTP is: {otp}
----------------------------------------------------

⏳ This OTP is valid for 10 minutes only.

Please do not share this OTP with anyone for security reasons.

If you did not request this registration, you can safely ignore this email.

Best regards,  
Support Team
"""

        send_mail(msg)

        return redirect("/verify-business-otp")

    return render_template("business_register.html")
@app.route("/verify-business-otp", methods=["GET","POST"])
def verify_business_otp():

    if request.method == "POST":

        user_otp = request.form["otp"]
        temp = session.get("temp_business")

        if not temp:
            return "Session expired. Register again."

        if user_otp == temp["otp"]:

            db = get_db()
            cur = db.cursor()

            # Check duplicate email
            cur.execute("SELECT * FROM users WHERE email=%s", (temp["email"],))
            if cur.fetchone():
                return "Email already exists"

            # Insert into DB
            cur.execute("""
                INSERT INTO users
                (business_name,email,phone,address,udyam_no,password,document,role,status)
                VALUES(%s,%s,%s,%s,%s,%s,%s,'business','pending')
            """, (
                temp["business_name"],
                temp["email"],
                temp["phone"],
                temp["address"],
                temp["udyam_no"],
                temp["password"],
                temp["document"]
            ))

            db.commit()

            session.pop("temp_business", None)

            return "Registration successful. Wait for admin approval."

        else:
            return "Invalid OTP"

    return render_template("verify_business_otp.html")
# LOGIN

@app.route("/login", methods=["GET","POST"])
def login():

    if request.method == "POST":

        email = request.form["email"]
        password = request.form["password"]

        db = get_db()
        cur = db.cursor(dictionary=True)

        cur.execute("SELECT * FROM users WHERE email=%s", (email,))
        user = cur.fetchone()

        if not user:
            return "Invalid Email or Password"

        db_password = user.get("password")

        if not db_password:
            return "Password not set"

        if not check_password_hash(db_password, password):
            return "Invalid Email or Password"

        session["user_id"] = user["id"]
        session["role"] = user["role"]

        if user["role"] == "admin":
            return redirect("/admin/dashboard")

        elif user["role"] == "business":
            if user["status"] != "approved":
                session.clear()
                return "Waiting for admin approval"
            return redirect("/business/dashboard")

        else:
            return redirect("/personal/dashboard")

    return render_template("login.html")

# Forgot Password Route
@app.route("/forgot-password", methods=["GET","POST"])
def forgot_password():

    if request.method == "POST":
        email = request.form["email"]

        db = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute("SELECT * FROM users WHERE email=%s", (email,))
        user = cur.fetchone()

        if not user:
            return "Email not found"

        otp = str(random.randint(100000, 999999))

        session["reset"] = {"email": email, "otp": otp}

        msg = Message("Password Reset OTP",
                      sender=app.config['MAIL_USERNAME'],
                      recipients=[email])
        msg.body = f"""
Hello,

We received a request to reset your account password.

To proceed with the password reset, please use the One-Time Password (OTP) below:

----------------------------------------------------
Your OTP is: {otp}
----------------------------------------------------

⏳ This OTP is valid for 10 minutes only.

If you did not request a password reset, please ignore this email. Your account is safe and no changes will be made.

For security reasons, never share this OTP with anyone.

Thank you,  
Support Team
"""
        send_mail(msg)

        return redirect("/verify-reset-otp")

    return render_template("forgot_password.html")
# Verify OTP
@app.route("/verify-reset-otp", methods=["GET","POST"])
def verify_reset_otp():

    if request.method == "POST":
        otp = request.form["otp"]

        if otp == session.get("reset", {}).get("otp"):
            return redirect("/reset-password")
        else:
            return "Invalid OTP"

    return render_template("verify_reset_otp.html")
# Reset Password
@app.route("/reset-password", methods=["GET","POST"])
def reset_password():

    if request.method == "POST":
        password = request.form["password"]
        confirm = request.form["confirm_password"]

        # ✅ Check match
        if password != confirm:
            return "Passwords do not match"

        # ✅ Check session email exists
        email = session.get("reset", {}).get("email")
        if not email:
            return "Session expired. Try again"

        # 🔐 Hash password
        hashed = generate_password_hash(password)

        db = get_db()
        cur = db.cursor()

        cur.execute(
            "UPDATE users SET password=%s WHERE email=%s",
            (hashed, email)
        )
        db.commit()

        # ✅ Clear session
        session.pop("reset", None)

        return redirect("/login")

    return render_template("reset_password.html")
# ADMIN DASHBOARD
@app.route("/admin/dashboard")
def admin_dashboard():

    # 🔒 Check login + role
    if not session.get("user_id") or session.get("role") != "admin":
        return redirect("/login")

    db = get_db()
    cur = db.cursor(dictionary=True)

    # Get pending business users
    cur.execute("""
        SELECT * FROM users
        WHERE role='business' AND status='pending'
        ORDER BY id DESC
    """)
    sellers = cur.fetchall()

    response = render_template("admin/dashboard.html", sellers=sellers)

    return response

# ADMIN APPROVE BUSINESS
@app.route("/admin/business_approval")
def business_approval():

    db = get_db()
    cur = db.cursor(dictionary=True)

    # ✅ ONLY PENDING BUSINESS USERS
    cur.execute("SELECT * FROM users WHERE role='business' AND status='pending'")
    users = cur.fetchall()

    return render_template("admin/business_approval.html", users=users)

@app.route("/approve-business/<int:user_id>")
def approve_business(user_id):

    db = get_db()
    cur = db.cursor()

    cur.execute("UPDATE users SET status='approved' WHERE id=%s", (user_id,))
    db.commit()

    return "success"


@app.route("/reject-business/<int:user_id>")
def reject_business(user_id):

    db = get_db()
    cur = db.cursor()

    cur.execute("UPDATE users SET status='rejected' WHERE id=%s", (user_id,))
    db.commit()

    return "success"

@app.route("/admin/wallet_requests")
def wallet_requests():

    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute("""
        SELECT wr.*, u.name, u.business_name, u.role
        FROM wallet_requests wr
        LEFT JOIN users u ON wr.user_id = u.id
        ORDER BY wr.id DESC
    """)

    requests = cur.fetchall()

    return render_template("admin/wallet_requests.html", requests=requests)

@app.route("/admin/approve_wallet/<int:id>")
def approve_wallet(id):

    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute("SELECT * FROM wallet_requests WHERE id=%s", (id,))
    req = cur.fetchone()

    if not req:
        return "Request not found"

    # ❌ Prevent double processing
    if req["status"] != "pending":
        return redirect("/admin/wallet_requests")

    # ✅ Add balance
    cur.execute("""
        UPDATE users
        SET wallet_balance = wallet_balance + %s
        WHERE id=%s
    """, (req["amount"], req["user_id"]))

    # ✅ Update status
    cur.execute("""
        UPDATE wallet_requests
        SET status='approved'
        WHERE id=%s
    """, (id,))

    db.commit()

    return redirect("/admin/wallet_requests")
@app.route("/admin/reject_wallet/<int:id>")
def reject_wallet(id):

    db = get_db()
    cur = db.cursor(dictionary=True)

    # check request exists
    cur.execute("SELECT * FROM wallet_requests WHERE id=%s", (id,))
    req = cur.fetchone()

    if not req:
        return "Request not found"

    # ❌ Prevent deleting approved request (safety)
    if req["status"] == "approved":
        return redirect("/admin/wallet_requests")

    # ✅ DELETE row
    cur.execute("DELETE FROM wallet_requests WHERE id=%s", (id,))

    db.commit()

    return redirect("/admin/wallet_requests")
# total amount on admin dashboard
@app.route("/admin/total_wallet_amount")
def total_wallet_amount():
    db = get_db()
    cur = db.cursor(dictionary=True)

    # Sum of wallet balances of all businesses
    cur.execute("""
        SELECT SUM(wallet_balance) AS total_amount
        FROM users
        WHERE role='business'
    """)
    result = cur.fetchone()
    total_amount = result["total_amount"] if result and result["total_amount"] else 0

    return render_template("admin/total_wallet_amount.html", total_amount=total_amount)
# ADMIN Users
@app.route("/admin/users")
def approved_users():

    db = get_db()
    cur = db.cursor(dictionary=True)

    # ✅ ONLY APPROVED BUSINESS + PERSONAL USERS
    cur.execute("""
        SELECT * FROM users 
        WHERE status='approved' 
        AND role IN ('business','personal')
    """)

    users = cur.fetchall()   # ✅ inside function

    return render_template("admin/users.html", users=users)
# ADMIN - VIEW ALL PROPERTIES
@app.route("/admin/properties")
def admin_properties():

    db = get_db()
    cur = db.cursor(dictionary=True)

    # ✅ Get all properties with owner info
    cur.execute("""
        SELECT p.*, u.business_name, u.name 
        FROM properties p
        LEFT JOIN users u ON p.user_id = u.id
    """)

    properties = cur.fetchall()

    return render_template("admin/properties.html", properties=properties)


@app.route("/admin/materials")
def admin_materials():
    if session.get("role") != "admin":
        return redirect("/login")

    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute("""
        SELECT m.*, u.business_name, u.name
        FROM materials m
        LEFT JOIN users u ON m.user_id = u.id
        ORDER BY m.id DESC
    """)
    materials = cur.fetchall()

    return render_template("admin/materials.html", materials=materials)


@app.route("/admin/materials/<int:material_id>/delete", methods=["POST"])
def admin_material_delete(material_id):
    if session.get("role") != "admin":
        return redirect("/login")

    db = get_db()
    cur = db.cursor()
    try:
        cur.execute("DELETE FROM materials WHERE id=%s", (material_id,))
        db.commit()
        flash("🗑️ Material deleted", "success")
    except Exception as e:
        db.rollback()
        print("DELETE MATERIAL ERROR:", e)
        flash("Could not delete material", "danger")

    return redirect("/admin/materials")


@app.route("/admin/offers")
def admin_offers():
    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute("""
        SELECT o.*,
               u1.name AS winner1_name,
               u2.name AS winner2_name,
               u3.name AS winner3_name
        FROM offers o
        LEFT JOIN users u1 ON o.winner_1 = u1.id
        LEFT JOIN users u2 ON o.winner_2 = u2.id
        LEFT JOIN users u3 ON o.winner_3 = u3.id
        ORDER BY o.id DESC
    """)

    offers = cur.fetchall()

    return render_template("admin/offers.html", offers=offers)

@app.route("/admin/offers/create", methods=["POST"])
def create_offer():

    title = request.form["title"]
    expiry = request.form["expiry"]
    image = request.files["image"]

    filename = secure_filename(image.filename)
    image.save(os.path.join("static/uploads", filename))

    db = get_db()
    cur = db.cursor()

    cur.execute(
        "INSERT INTO offers (title, amount, image, expiry) VALUES (%s,%s,%s,%s)",
        (title, 0, filename, expiry)
    )

    db.commit()

    return redirect("/admin/offers")


@app.route("/admin/offers/<int:offer_id>/delete", methods=["POST"])
def delete_offer(offer_id):
    if session.get("role") != "admin":
        return redirect("/login")

    db = get_db()
    cur = db.cursor()
    try:
        cur.execute("DELETE FROM offer_enrollments WHERE offer_id=%s", (offer_id,))
        cur.execute("DELETE FROM offers WHERE id=%s", (offer_id,))
        db.commit()
        flash("🗑️ Offer deleted", "success")
    except Exception as e:
        db.rollback()
        print("DELETE OFFER ERROR:", e)
        flash("Could not delete offer", "danger")
    finally:
        cur.close()
        db.close()

    return redirect("/admin/offers")
@app.route("/admin/offers/<int:offer_id>/users")
def admin_offer_users(offer_id):

    if not session.get("user_id") or session.get("role") != "admin":
        return redirect("/login")

    db = get_db()
    cur = db.cursor(dictionary=True)

    # 🔹 Get Offer Info
    cur.execute("SELECT * FROM offers WHERE id = %s", (offer_id,))
    offer = cur.fetchone()

    # 🔹 Get Enrolled Users
    cur.execute("""
        SELECT u.id, u.name, u.business_name, u.email, u.phone, oe.created_at
        FROM offer_enrollments oe
        JOIN users u ON oe.user_id = u.id
        WHERE oe.offer_id = %s
        ORDER BY oe.created_at DESC
    """, (offer_id,))

    users = cur.fetchall()

    return render_template("admin/offer_users.html", users=users, offer=offer)
#offer date extend
@app.route("/admin/offers/<int:offer_id>/extend", methods=["POST"])
def extend_offer(offer_id):

    if session.get("role") != "admin":
        return redirect("/login")

    new_expiry = request.form.get("new_expiry")

    db = get_db()
    cur = db.cursor()

    cur.execute("""
        UPDATE offers 
        SET expiry = %s 
        WHERE id = %s
    """, (new_expiry, offer_id))

    db.commit()
    return redirect("/admin/offers")
# offer result 

# @app.route("/admin/offers/<int:offer_id>/declare", methods=["POST"])
# def declare_winners(offer_id):

#     if session.get("role") != "admin":
#         return redirect("/login")

#     db = get_db()
#     cur = db.cursor(dictionary=True)

#     # Get all enrolled users
#     cur.execute("""
#         SELECT user_id FROM offer_enrollments 
#         WHERE offer_id = %s
#     """, (offer_id,))
    
#     users = cur.fetchall()

#     if len(users) < 3:
#         return "Minimum 3 users required"

#     # Pick 3 unique winners
#     selected = random.sample(users, 3)

#     w1 = selected[0]["user_id"]
#     w2 = selected[1]["user_id"]
#     w3 = selected[2]["user_id"]

#     # Update DB
#     cur.execute("""
#         UPDATE offers 
#         SET winner_1=%s, winner_2=%s, winner_3=%s, status='completed'
#         WHERE id=%s
#     """, (w1, w2, w3, offer_id))

#     db.commit()

#     return redirect("/admin/offers")
# result declare with manuly
@app.route("/admin/offers/<int:offer_id>/declare_manual", methods=["POST"])
def declare_manual_winners(offer_id):

    if session.get("role") != "admin":
        return redirect("/login")

    rank1 = request.form.get("rank1")
    rank2 = request.form.getlist("rank2")
    rank3 = request.form.getlist("rank3")

    print(rank1, rank2, rank3)  # 🔥 DEBUG

    if not rank1:
        return "Select 1st winner"

    if not rank2:
        return "Select at least one 2nd rank winner"

    if not rank3:
        return "Select at least one 3rd rank winner"

    db = get_db()
    cur = db.cursor()

    cur.execute("""
        UPDATE offers 
        SET 
            winner_1=%s,
            winner_2=%s,
            winner_3=%s,
            status='completed'
        WHERE id=%s
    """, (
        rank1,
        ",".join(rank2),
        ",".join(rank3),
        offer_id
    ))

    db.commit()

    return redirect("/admin/offers")
@app.route("/admin/offers/<int:offer_id>/close", methods=["POST"])
def close_offer(offer_id):

    if session.get("role") != "admin":
        return redirect("/login")

    db = get_db()
    cur = db.cursor()

    # Offer close करणे
    cur.execute("""
        UPDATE offers 
        SET status = 'closed'
        WHERE id = %s
    """, (offer_id,))

    db.commit()

    return redirect("/admin/offers")
# admin profile section
@app.route("/admin/profile")
def admin_profile():

    # security check
    if session.get("role") != "admin":
        return redirect("/login")

    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute("""
        SELECT id, name, email, phone, status, profile_pic
        FROM users
        WHERE id = %s AND role = 'admin'
    """, (session["user_id"],))

    admin = cur.fetchone()

    if not admin:
        session.clear()
        return redirect("/login")

    cur.execute("""
        SELECT COALESCE(SUM(amount),0) AS total_received
        FROM wallet_transactions
        WHERE status = 'approved'
    """)

    total_received = cur.fetchone()["total_received"]

    return render_template(
        "admin/profile.html",
        admin=admin,
        total_received=total_received
    )
# BUSINESS DASHBOARD
@app.route("/business/dashboard")
def business_dashboard():

    if session.get("role") != "business":
        return redirect("/login")

    db = get_db()
    cur = db.cursor(dictionary=True)

    # Get properties
    cur.execute("SELECT * FROM properties WHERE user_id=%s",(session["user_id"],))
    properties = cur.fetchall()

    # Get wallet balance
    cur.execute("SELECT wallet_balance FROM users WHERE id=%s",(session["user_id"],))
    wallet = cur.fetchone()

    return render_template(
        "business/dashboard.html",
        properties=properties,
        wallet=wallet
    )

# ADD PROPERTY
# ADD PROPERTY
@app.route("/business/add_property", methods=["GET","POST"])
def add_property():

    if session.get("role") != "business":
        return redirect("/login")

    if request.method == "POST":

        title = request.form["title"]
        property_type = request.form["property_type"]

        flat_type = request.form.get("flat_type")
        plot_size = request.form.get("plot_size")

        price = request.form["price"]
        city = request.form["city"]
        area = request.form["area"]

        contact_number = request.form["contact_number"]
        map_link = request.form.get("map_link")

        description = request.form["description"]

        # CREATE LOCATION
        location = city + " - " + area

        # ✅ IMAGE UPLOAD (2 IMAGES)
        image1 = request.files.get("image1")
        image2 = request.files.get("image2")

        filename1 = ""
        filename2 = ""

        upload_path = "static/uploads"
        os.makedirs(upload_path, exist_ok=True)

        if image1 and image1.filename != "":
            filename1 = secure_filename(image1.filename)
            image1.save(os.path.join(upload_path, filename1))

        if image2 and image2.filename != "":
            filename2 = secure_filename(image2.filename)
            image2.save(os.path.join(upload_path, filename2))

        db = get_db()
        cur = db.cursor(dictionary=True)

        # CHECK WALLET BALANCE + PLAN
        cur.execute(
            "SELECT wallet_balance, plan_name, plan_expiry, top_listings_left FROM users WHERE id=%s",
            (session["user_id"],)
        )
        user_row = cur.fetchone() or {}
        wallet = user_row.get("wallet_balance") or 0

        if wallet < 10:
            return "Insufficient wallet balance"

        # DEDUCT WALLET ₹10
        cur.execute(
            "UPDATE users SET wallet_balance = wallet_balance - 10 WHERE id=%s",
            (session["user_id"],)
        )
        wallet -= 10

        # 🌟 TOP LISTING (₹100 add-on or plan credit)
        top_listing_requested = request.form.get("top_listing") == "1"
        is_top_listing = 0
        plan_active = bool(
            user_row.get("plan_name") and user_row.get("plan_expiry")
            and user_row["plan_expiry"] >= datetime.now()
        )
        if top_listing_requested:
            if plan_active and (user_row.get("top_listings_left") or 0) > 0:
                cur.execute("UPDATE users SET top_listings_left = top_listings_left - 1 WHERE id=%s", (session["user_id"],))
                is_top_listing = 1
            elif wallet >= 100:
                cur.execute("UPDATE users SET wallet_balance = wallet_balance - 100 WHERE id=%s", (session["user_id"],))
                is_top_listing = 1
            else:
                flash("⚠️ Property posted, but Top Listing needs ₹100 (insufficient balance/credits) — it was skipped.")

        # ✅ INSERT PROPERTY (image2 add kelay)
        cur.execute("""
        INSERT INTO properties
        (user_id,title,price,location,property_type,flat_type,plot_size,map_link,image1,image2,description,listing_type,is_top_listing,status,contact_number)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'sell',%s,'active',%s)
        """,
        (
            session["user_id"],
            title,
            price,
            location,
            property_type,
            flat_type,
            plot_size,
            map_link,
            filename1,
            filename2,
            description,
            is_top_listing,
            contact_number
        ))

        db.commit()

        return redirect("/business/dashboard")

    return render_template("business/add_property.html")

@app.route("/business/my_properties")
def business_my_properties():
    if session.get("role") != "business":
        return redirect("/login")

    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("""
        SELECT *, COALESCE(status,'active') AS status
        FROM properties WHERE user_id=%s ORDER BY id DESC
    """, (session["user_id"],))
    properties = cur.fetchall()
    cur.close()
    db.close()

    return render_template("business/my_properties.html", properties=properties)


# ADD MATERIAL
@app.route("/business/add_material", methods=["GET", "POST"])
def add_material():

    # 🔒 Role Check
    if session.get("role") != "business":
        return redirect("/login")

    if request.method == "POST":

        # 📥 FORM DATA
        material_type = request.form.get("material_type")
        cement_company = request.form.get("cement_company")

        # 🧠 SMART TITLE GENERATE
        if material_type == "cement":
            title = f"Cement ({cement_company})" if cement_company else "Cement"
        elif material_type == "sand":
            title = "Sand"
        elif material_type == "bricks":
            title = "Bricks"
        else:
            title = request.form.get("title") or "Material"

        price = request.form.get("price")
        quantity = request.form.get("quantity")
        location = request.form.get("location")
        map_link = request.form.get("map_link")
        description = request.form.get("description")

        # ⚠️ BASIC VALIDATION
        if not price or not location:
            flash("Price and Location are required!", "danger")
            return redirect("/business/add_material")

        # 📸 IMAGE UPLOAD (SAFE)
        file = request.files.get("image1")
        filename = ""

        if file and file.filename != "":
            filename = secure_filename(file.filename)

            upload_path = os.path.join("static", "uploads")
            os.makedirs(upload_path, exist_ok=True)

            file.save(os.path.join(upload_path, filename))

        # 💾 DATABASE INSERT
        db = get_db()
        cur = db.cursor()

        cur.execute("""
            INSERT INTO materials
            (user_id, title, price, quantity, location, map_link, image1, description, category)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            session["user_id"],
            title,
            price,
            quantity,
            location,
            map_link,
            filename,
            description,
            material_type
        ))

        db.commit()
        cur.close()
        db.close()

        flash("✅ Material added successfully!", "success")
        return redirect("/business/dashboard")

    return render_template("business/add_material.html")

def _get_plan_status(cur, user_id):
    """Returns a dict describing the user's current active Recharge Plan
    (name, price, days left, listings used/remaining) or None if they don't
    have one. Shared by both personal and business wallet pages."""
    cur.execute("""
        SELECT plan_name, plan_expiry, sell_listings_left, rent_listings_left,
               top_listings_left, free_offer_entries
        FROM users WHERE id=%s
    """, (user_id,))
    plan_row = cur.fetchone() or {}

    raw_expiry = plan_row.get("plan_expiry")
    expiry_dt = None
    if raw_expiry:
        if isinstance(raw_expiry, str):
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    expiry_dt = datetime.strptime(raw_expiry, fmt)
                    break
                except ValueError:
                    continue
        else:
            expiry_dt = raw_expiry

    plan_active = bool(plan_row.get("plan_name") and expiry_dt and expiry_dt >= datetime.now())
    if not plan_active:
        return None

    plan_key = plan_row["plan_name"]
    catalog_entry = PLAN_CATALOG.get(plan_key, {})
    total_sell = catalog_entry.get("sell", 0)
    total_rent = catalog_entry.get("rent", 0)
    sell_left = plan_row.get("sell_listings_left") or 0
    rent_left = plan_row.get("rent_listings_left") or 0

    return {
        "name": plan_key,
        "price": catalog_entry.get("price"),
        "expiry": expiry_dt,
        "days_left": max((expiry_dt - datetime.now()).days, 0),
        "unlimited": total_sell == -1,
        "sell_left": sell_left,
        "sell_total": total_sell,
        "sell_used": (total_sell - sell_left) if total_sell not in (-1, None) else 0,
        "rent_left": rent_left,
        "rent_total": total_rent,
        "rent_used": (total_rent - rent_left) if total_rent not in (-1, None) else 0,
        "top_listings_left": plan_row.get("top_listings_left") or 0,
        "free_offer_entries": plan_row.get("free_offer_entries") or 0,
    }


@app.route("/business/wallet")
def business_wallet():

    if session.get("role") != "business":
        return redirect("/login")

    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute("SELECT wallet_balance FROM users WHERE id=%s",(session["user_id"],))
    wallet = cur.fetchone()

    current_plan = _get_plan_status(cur, session["user_id"])

    cur.execute("""
    SELECT * FROM wallet_requests
    WHERE user_id=%s
    ORDER BY id DESC
    """,(session["user_id"],))

    requests = cur.fetchall()

    return render_template(
        "business/wallet.html",
        wallet=wallet,
        current_plan=current_plan,
        requests=requests
    )
@app.route("/business/add_wallet", methods=["POST"])
def add_wallet():

    amount = request.form["amount"]
    utr = request.form["utr"]

    file = request.files["screenshot"]
    filename = file.filename
    file.save("static/uploads/"+filename)

    db = get_db()
    cur = db.cursor()

    cur.execute("""
    INSERT INTO wallet_requests(user_id,amount,utr_number,payment_screenshot)
    VALUES(%s,%s,%s,%s)
    """,(session["user_id"],amount,utr,filename))

    db.commit()

    return redirect("/business/wallet")
# offer section
@app.route("/business/offers")
def business_offers():

    if not session.get("user_id") or session.get("role") != "business":
        return redirect("/login")

    user_id = session.get("user_id")

    db = get_db()
    cur = db.cursor(dictionary=True)

    # 🔹 TOTAL USERS (same logic)
    cur.execute("SELECT COUNT(*) AS total_users FROM users")
    total_users = cur.fetchone()["total_users"] or 1

    # -------------------------
    # ACTIVE OFFERS
    # -------------------------
    cur.execute("""
    SELECT 
        o.*, 
        COUNT(e.id) AS total_enrolled,

        u1.name AS winner1_name,
        u2.name AS winner2_name,
        u3.name AS winner3_name

    FROM offers o
    LEFT JOIN offer_enrollments e ON o.id = e.offer_id
    LEFT JOIN users u1 ON o.winner_1 = u1.id
    LEFT JOIN users u2 ON o.winner_2 = u2.id
    LEFT JOIN users u3 ON o.winner_3 = u3.id

    WHERE o.expiry >= NOW()

    GROUP BY o.id
    ORDER BY o.expiry ASC
    """)
    active_offers = cur.fetchall()

    # -------------------------
    # HISTORY (ONLY ENROLLED)
    # -------------------------
    cur.execute("""
    SELECT 
        o.*, 
        COUNT(e.id) AS total_enrolled,

        u1.name AS winner1_name,
        u2.name AS winner2_name,
        u3.name AS winner3_name

    FROM offers o
    LEFT JOIN offer_enrollments e ON o.id = e.offer_id
    LEFT JOIN users u1 ON o.winner_1 = u1.id
    LEFT JOIN users u2 ON o.winner_2 = u2.id
    LEFT JOIN users u3 ON o.winner_3 = u3.id

    WHERE o.expiry < NOW()
    AND o.id IN (
        SELECT offer_id FROM offer_enrollments WHERE user_id = %s
    )

    GROUP BY o.id
    ORDER BY o.expiry DESC
    """, (user_id,))
    history_offers = cur.fetchall()

    # -------------------------
    # CALCULATION
    # -------------------------
    def process(offers_list):
        for offer in offers_list:
            enrolled_count = offer["total_enrolled"] or 0

            offer["total_users"] = total_users
            offer["progress"] = int((enrolled_count * 100) / total_users)

            left = total_users - enrolled_count
            offer["spots_left"] = left if left > 0 else 0

            # consistency safety net (same fix as personal/offers)
            expiry = offer.get("expiry")
            if expiry:
                delta = expiry - datetime.now()
                offer["days_left"] = max(delta.days, 0)
            else:
                offer["days_left"] = 0

    process(active_offers)
    process(history_offers)

    # -------------------------
    # ENROLLED LIST
    # -------------------------
    cur.execute("""
        SELECT offer_id FROM offer_enrollments 
        WHERE user_id = %s
    """, (user_id,))
    
    enrolled = [row["offer_id"] for row in cur.fetchall()]

    # 🔒 ONE OFFER AT A TIME
    cur.execute("""
        SELECT o.id, o.title
        FROM offer_enrollments oe
        JOIN offers o ON oe.offer_id = o.id
        WHERE oe.user_id = %s AND o.expiry >= NOW()
        LIMIT 1
    """, (user_id,))
    active_enrollment = cur.fetchone()

    # 🔒 PLAN CHECK for the template
    cur.execute("SELECT plan_name, plan_expiry FROM users WHERE id = %s", (user_id,))
    plan_row = cur.fetchone() or {}
    raw_expiry = plan_row.get("plan_expiry")
    expiry_dt = None
    if raw_expiry:
        if isinstance(raw_expiry, str):
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    expiry_dt = datetime.strptime(raw_expiry, fmt)
                    break
                except ValueError:
                    continue
        else:
            expiry_dt = raw_expiry
    plan_active = bool(plan_row.get("plan_name") and expiry_dt and expiry_dt >= datetime.now())

    return render_template(
        "business/offers.html",   # 🔥 IMPORTANT CHANGE
        offers=active_offers,
        history_offers=history_offers,
        enrolled=enrolled,
        active_enrollment=active_enrollment,
        plan_active=plan_active
    )

# ==============================
# 🔹 BUSINESS PROFILE
# ==============================
@app.route("/business/profile")
def business_profile():
    if session.get("role") != "business":
        return redirect("/login")

    db = get_db()
    cur = db.cursor(dictionary=True)

    try:
        cur.execute("""
            SELECT id, name, email, phone, address, wallet_balance, profile_pic, business_name
            FROM users
            WHERE id = %s
        """, (session["user_id"],))
        user = cur.fetchone()

        if not user:
            flash("User not found", "danger")
            return redirect("/login")

        cur.execute("""
            SELECT id, title, price, location, image1
            FROM properties
            WHERE user_id = %s ORDER BY id DESC
        """, (session["user_id"],))
        properties = cur.fetchall()

        cur.execute("""
            SELECT id, title, price, quantity
            FROM materials
            WHERE user_id = %s ORDER BY id DESC
        """, (session["user_id"],))
        materials = cur.fetchall()

    except Exception as e:
        print("BUSINESS PROFILE ERROR:", e)
        flash("Something went wrong!", "danger")
        return redirect("/business/dashboard")
    finally:
        cur.close()
        db.close()

    return render_template(
        "business/profile.html",
        user=user,
        properties=properties,
        materials=materials
    )

@app.route("/business/update-profile", methods=["POST"])
def business_update_profile():
    if session.get("role") != "business":
        return redirect("/login")

    user_id = session["user_id"]
    name = request.form.get("name")
    phone = request.form.get("phone")
    address = request.form.get("address")
    business_name = request.form.get("business_name")
    new_password = request.form.get("new_password")

    db = get_db()
    cur = db.cursor()
    try:
        if new_password and new_password.strip():
            hashed = generate_password_hash(new_password)
            cur.execute("""
                UPDATE users SET name=%s, phone=%s, address=%s, business_name=%s, password=%s
                WHERE id=%s
            """, (name, phone, address, business_name, hashed, user_id))
        else:
            cur.execute("""
                UPDATE users SET name=%s, phone=%s, address=%s, business_name=%s
                WHERE id=%s
            """, (name, phone, address, business_name, user_id))
        db.commit()
        flash("Profile updated successfully!", "success")
    except Exception as e:
        db.rollback()
        print("ERROR:", e)
        flash("Something went wrong!", "danger")
    finally:
        cur.close()
        db.close()

    return redirect("/business/profile")

@app.route("/business/update-profile-pic", methods=["POST"])
def business_update_profile_pic():
    if session.get("role") != "business":
        return redirect("/login")

    file = request.files.get("profile_pic")
    if file and file.filename != "":
        filename = secure_filename(file.filename)
        filename = f"biz_{session['user_id']}_{filename}"
        upload_path = "static/uploads"
        os.makedirs(upload_path, exist_ok=True)
        file.save(os.path.join(upload_path, filename))

        db = get_db()
        cur = db.cursor()
        cur.execute("UPDATE users SET profile_pic=%s WHERE id=%s", (filename, session["user_id"]))
        db.commit()
        cur.close()
        db.close()

    return redirect("/business/profile")

# personal dashboard
@app.route("/personal/dashboard")
def personal_dashboard():

    if not session.get("user_id") or session.get("role") != "personal":
        return redirect("/login")

    db = get_db()
    cur = db.cursor(dictionary=True)

    # 👤 USER FETCH (NEW ADD)
    cur.execute("""
        SELECT name, profile_pic 
        FROM users 
        WHERE id = %s
    """, (session["user_id"],))
    user = cur.fetchone()

    # 🏠 TOP LISTING PROPERTIES (only ones the owner selected/paid for — NOT every post)
    cur.execute("""
        SELECT 
            properties.*,
            users.business_name,
            users.phone AS user_phone
        FROM properties
        LEFT JOIN users ON properties.user_id = users.id
        WHERE properties.is_top_listing = 1
          AND COALESCE(properties.status,'active') = 'active'
        ORDER BY properties.id DESC
        LIMIT 10
    """)
    properties = cur.fetchall()

    # 🧱 MATERIALS
    cur.execute("""
        SELECT 
            materials.*,
            users.business_name,
            users.phone AS user_phone
        FROM materials
        LEFT JOIN users ON materials.user_id = users.id
        ORDER BY materials.id DESC
    """)
    materials = cur.fetchall()

    return render_template(
        "personal/dashboard.html",
        properties=properties,
        materials=materials,
        user=user   # ✅ VERY IMPORTANT
    )


# @app.route("/personal/add_rent", methods=["GET", "POST"])
# def personal_add_rent():

#     if not session.get("user_id") or session.get("role") != "personal":
#         return redirect("/login")

#     if request.method == "POST":

#         title = request.form.get("title")
#         property_type = request.form.get("flat_type")   # room / flat
#         price = request.form.get("price")
#         city = request.form.get("city")
#         area = request.form.get("area")
#         description = request.form.get("description")

#         location = f"{city} - {area}"

#         # 📸 IMAGE UPLOAD
#         file = request.files.get("image1")
#         filename = None

#         if file and file.filename:
#             filename = file.filename
#             file.save("static/uploads/" + filename)

#         db = get_db()
#         cur = db.cursor(dictionary=True)

#         # 💰 WALLET CHECK
#         cur.execute(
#             "SELECT wallet_balance FROM users WHERE id=%s",
#             (session["user_id"],)
#         )
#         wallet_row = cur.fetchone()
#         wallet = wallet_row["wallet_balance"] if wallet_row else 0

#         if wallet < 10:
#             flash("❌ Insufficient wallet balance")
#             return redirect("/personal/wallet")

#         # 💸 WALLET DEDUCT
#         cur.execute("""
#             UPDATE users
#             SET wallet_balance = wallet_balance - 10
#             WHERE id=%s
#         """, (session["user_id"],))

#         # 🏠 INSERT PROPERTY (NO contact_number needed)
#         cur.execute("""
#             INSERT INTO properties
#             (user_id, title, price, location, property_type, image1, description)
#             VALUES (%s, %s, %s, %s, %s, %s, %s)
#         """, (
#             session["user_id"],
#             title,
#             price,
#             location,
#             property_type,
#             filename,
#             description
#         ))

#         db.commit()

#         flash("✅ Rent added successfully!")
#         return redirect("/personal/dashboard")

#     return render_template("personal/add_rent.html")

from datetime import datetime, timedelta
import os
@app.route("/personal/add_property", methods=["GET", "POST"])
def personal_add_property():

    # 🔐 Auth check
    if not session.get("user_id") or session.get("role") != "personal":
        return redirect("/login")

    db = get_db()
    cur = db.cursor(dictionary=True)

    if request.method == "POST":

        # 🔁 COMMON DATA
        purpose = request.form.get("purpose")  # Sell / Rent
        title = request.form.get("title")
        property_type = request.form.get("property_type")
        flat_type = request.form.get("flat_type")
        price = request.form.get("price")
        city = request.form.get("city")
        locality = request.form.get("locality")
        full_address = request.form.get("address")
        area_value = request.form.get("area")       # sqft/sqyd NUMBER — was wrongly being used as the location text
        area_unit = request.form.get("area_unit")    # sqft / sqyard / acre etc.
        map_link = request.form.get("map_link")
        description = request.form.get("description")

        # 🔧 FIX: build a proper human-readable location from city + locality
        # (both were being collected on the form but locality was never read
        # at all — the old code used the numeric "area" field by mistake,
        # producing locations like "Pune - 1200" instead of "Kothrud, Pune").
        location_parts = [p for p in [locality, city] if p and p.strip()]
        location = ", ".join(location_parts) if location_parts else (city or "—")

        # 📸 IMAGE
        file = request.files.get("image1")
        filename = None

        if file and file.filename:
            filename = secure_filename(file.filename)
            file.save(os.path.join("static/uploads", filename))

        # 🌟 TOP LISTING (₹100 add-on, OR consumes a Premium plan credit)
        top_listing_requested = request.form.get("top_listing") == "1"

        # 💰 WALLET + PLAN CHECK (COMMON)
        cur.execute("""
            SELECT wallet_balance, plan_name, plan_expiry,
                   sell_listings_left, rent_listings_left, top_listings_left
            FROM users WHERE id=%s
        """, (session["user_id"],))
        user_row = cur.fetchone() or {}
        wallet = user_row.get("wallet_balance") or 0

        plan_active = bool(
            user_row.get("plan_name")
            and user_row.get("plan_expiry")
            and user_row["plan_expiry"] >= datetime.now()
        )

        # 🎁 LEGACY FREE LOGIC (kept for backward compatibility)
        cur.execute("""
            SELECT MAX(created_at) AS last_enroll
            FROM offer_enrollments
            WHERE user_id = %s
        """, (session["user_id"],))

        row = cur.fetchone()
        last_enroll = row["last_enroll"]

        free_allowed = False
        if last_enroll and (datetime.now() - last_enroll <= timedelta(days=365)):
            free_allowed = True

        # 📦 DETERMINE IF A RECHARGE-PLAN QUOTA COVERS THIS LISTING
        use_plan_quota = False
        quota_field = "sell_listings_left" if purpose != "Rent" else "rent_listings_left"

        if plan_active:
            if user_row.get("plan_name") == "999":
                # ₹999 plan = unlimited sell & rent listings
                use_plan_quota = True
            elif user_row.get("plan_name") == "499" and (user_row.get(quota_field) or 0) > 0:
                use_plan_quota = True

        # 💸 WALLET DEDUCT (₹10 per listing) — skipped if a plan quota covers it
        if use_plan_quota:
            if user_row.get("plan_name") == "499":
                cur.execute(
                    f"UPDATE users SET {quota_field} = {quota_field} - 1 WHERE id=%s",
                    (session["user_id"],)
                )
        elif not free_allowed:
            if wallet < 10:
                flash("❌ Insufficient wallet balance. Buy a Recharge Plan or add money to your wallet.")
                return redirect("/personal/wallet")

            cur.execute("""
                UPDATE users SET wallet_balance = wallet_balance - 10
                WHERE id=%s
            """, (session["user_id"],))
            wallet -= 10

        # 🌟 TOP LISTING CHARGE / CREDIT
        is_top_listing = 0
        if top_listing_requested:
            if plan_active and (user_row.get("top_listings_left") or 0) > 0:
                cur.execute(
                    "UPDATE users SET top_listings_left = top_listings_left - 1 WHERE id=%s",
                    (session["user_id"],)
                )
                is_top_listing = 1
            elif wallet >= 100:
                cur.execute(
                    "UPDATE users SET wallet_balance = wallet_balance - 100 WHERE id=%s",
                    (session["user_id"],)
                )
                is_top_listing = 1
            else:
                flash("⚠️ Property posted, but Top Listing needs ₹100 (insufficient balance/credits) — it was skipped.")

        # ================================
        # 🔴 RENT LOGIC
        # ================================
        if purpose == "Rent":

            deposit = request.form.get("deposit")

            final_type = flat_type if property_type == "flat" else property_type

            cur.execute("""
                INSERT INTO properties
                (user_id, title, price, deposit, location, property_type, map_link, image1, description, listing_type, is_top_listing, status, locality, area_value, area_unit, full_address)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s,%s,%s,%s)
            """, (
                session["user_id"],
                title,
                price,
                deposit,
                location,
                final_type,
                map_link,
                filename,
                description,
                "rent",
                is_top_listing,
                locality,
                area_value,
                area_unit,
                full_address
            ))

            flash("✅ Rent property added!" + (" 🌟 Now showing as Top Listing." if is_top_listing else ""))

        # ================================
        # 🔵 SELL LOGIC
        # ================================
        else:

            plot_size = request.form.get("plot_size")

            final_type = flat_type if property_type == "flat" else "plot"

            cur.execute("""
                INSERT INTO properties
                (user_id, title, price, location, property_type, plot_size, map_link, image1, description, listing_type, is_top_listing, status, locality, area_value, area_unit, full_address)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s,%s,%s,%s)
            """, (
                session["user_id"],
                title,
                price,
                location,
                final_type,
                plot_size,
                map_link,
                filename,
                description,
                "sell",
                is_top_listing,
                locality,
                area_value,
                area_unit,
                full_address
            ))

            flash("✅ Property posted for sale!" + (" 🌟 Now showing as Top Listing." if is_top_listing else ""))

        db.commit()
        return redirect("/personal/dashboard")

    return render_template("personal/add_property.html")
@app.route("/personal/wallet")
def personal_wallet():

    if session.get("role") != "personal":
        return redirect("/login")

    db = get_db()
    cur = db.cursor(dictionary=True)

    # wallet balance
    cur.execute("SELECT wallet_balance FROM users WHERE id=%s", (session["user_id"],))
    wallet = cur.fetchone()

    # 📦 CURRENT RECHARGE PLAN STATUS (active plan, days left, listings used/left)
    current_plan = _get_plan_status(cur, session["user_id"])

    # 👉 last 7 days filter
    last_7_days = datetime.now() - timedelta(days=7)

    cur.execute("""
        SELECT * FROM wallet_requests
        WHERE user_id=%s
        AND created_at >= %s
        ORDER BY id DESC
    """, (session["user_id"], last_7_days))

    requests = cur.fetchall()

    return render_template(
        "personal/wallet.html",
        current_plan=current_plan,
        wallet=wallet,
        requests=requests
    )


PLAN_CATALOG = {
    "499": {"price": 499, "sell": 25, "rent": 15, "top": 0, "free_offer": 0, "days": 30},
    "999": {"price": 999, "sell": -1, "rent": -1, "top": 3, "free_offer": 1, "days": 30},
    # legacy plan-id aliases used by older buttons in the UI
    "standard": {"price": 499, "sell": 25, "rent": 15, "top": 0, "free_offer": 0, "days": 30},
    "premium": {"price": 999, "sell": -1, "rent": -1, "top": 3, "free_offer": 1, "days": 30},
}


@app.route("/personal/buy_plan", methods=["POST"])
def personal_buy_plan():
    if session.get("role") != "personal":
        return redirect("/login")
    return _buy_plan(request.form.get("plan"), "/personal/wallet")


@app.route("/business/buy_plan", methods=["POST"])
def business_buy_plan():
    if session.get("role") != "business":
        return redirect("/login")
    return _buy_plan(request.form.get("plan"), "/business/wallet")


def _buy_plan(plan_key, redirect_url):
    plan = PLAN_CATALOG.get(plan_key)
    if not plan:
        flash("Invalid plan selected", "danger")
        return redirect(redirect_url)

    user_id = session["user_id"]
    db = get_db()
    cur = db.cursor(dictionary=True)
    try:
        cur.execute("SELECT wallet_balance FROM users WHERE id=%s", (user_id,))
        row = cur.fetchone()
        wallet = row["wallet_balance"] if row else 0

        if wallet < plan["price"]:
            flash(f"❌ Insufficient wallet balance. Add money and try again (need ₹{plan['price']}).", "danger")
            return redirect(redirect_url)

        plan_id = "999" if plan["sell"] == -1 else "499"
        expiry = datetime.now() + timedelta(days=plan["days"])

        cur.execute("""
            UPDATE users
            SET wallet_balance = wallet_balance - %s,
                plan_name = %s,
                plan_expiry = %s,
                sell_listings_left = %s,
                rent_listings_left = %s,
                top_listings_left = %s,
                free_offer_entries = %s
            WHERE id=%s
        """, (
            plan["price"], plan_id, expiry,
            plan["sell"], plan["rent"], plan["top"], plan["free_offer"],
            user_id
        ))
        db.commit()
        flash(f"✅ Recharge Plan ₹{plan['price']} activated! Valid for {plan['days']} days.", "success")
    except Exception as e:
        db.rollback()
        print("BUY PLAN ERROR:", e)
        flash("Something went wrong while purchasing the plan.", "danger")
    finally:
        cur.close()
        db.close()

    return redirect(redirect_url)


@app.route("/personal/add_wallet", methods=["POST"])
def personal_add_wallet():

    if session.get("role") != "personal":
        return redirect("/login")

    amount = request.form.get("amount")
    utr = request.form.get("utr")

    # ✅ UTR VALIDATION (12 digits only)
    if not re.fullmatch(r"\d{12}", utr or ""):
        flash("UTR must be exactly 12 digits", "danger")
        return redirect("/personal/wallet")

    db = get_db()
    cur = db.cursor()

    # ✅ DUPLICATE UTR CHECK (important 🔥)
    cur.execute("SELECT id FROM wallet_requests WHERE utr_number=%s", (utr,))
    if cur.fetchone():
        flash("This UTR already exists. Please check.", "warning")
        return redirect("/personal/wallet")

    # ✅ FILE UPLOAD (SAFE)
    file = request.files.get("screenshot")
    filename = ""

    if file and file.filename != "":
        filename = secure_filename(file.filename)

        upload_path = os.path.join("static/uploads", filename)
        file.save(upload_path)

    # ✅ INSERT
    cur.execute("""
        INSERT INTO wallet_requests
        (user_id, amount, utr_number, payment_screenshot)
        VALUES (%s,%s,%s,%s)
    """, (session["user_id"], amount, utr, filename))

    db.commit()

    flash("Payment request submitted successfully!", "success")

    return redirect("/personal/wallet")
# PERSONAL: All Properties Page
@app.route("/personal/properties")
def personal_properties():  # Unique function name
    if session.get("role") != "personal":
        return redirect("/login")

    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute("""
        SELECT properties.*, users.business_name, users.name AS poster_name,
               users.phone, users.profile_pic AS poster_pic,
               COALESCE(properties.status,'active') AS status
        FROM properties
        LEFT JOIN users ON properties.user_id = users.id
        WHERE COALESCE(properties.status,'active') = 'active'
        ORDER BY properties.id DESC
    """)
    properties = cur.fetchall()

    # My own properties (all statuses) for the "My Posts" tab
    cur.execute("""
        SELECT properties.*, users.business_name, users.name AS poster_name,
               users.phone, users.profile_pic AS poster_pic,
               COALESCE(properties.status,'active') AS status
        FROM properties
        LEFT JOIN users ON properties.user_id = users.id
        WHERE properties.user_id = %s
        ORDER BY properties.id DESC
    """, (session["user_id"],))
    my_properties = cur.fetchall()

    cur.close()
    db.close()
    return render_template("personal/properties.html",
                           properties=properties,
                           my_properties=my_properties,
                           current_user_id=session["user_id"])


# ==============================
# 🔹 PROPERTY STATUS ACTIONS
# ==============================
def _property_status_update(prop_id, new_status, allowed_roles=("personal","business")):
    """Helper: update property status, checking ownership."""
    role = session.get("role")
    if role not in allowed_roles:
        return {"ok": False, "msg": "Unauthorized"}
    db = get_db()
    cur = db.cursor(dictionary=True)
    try:
        cur.execute("SELECT user_id FROM properties WHERE id=%s", (prop_id,))
        row = cur.fetchone()
        if not row or row["user_id"] != session["user_id"]:
            return {"ok": False, "msg": "Not your property"}
        cur.execute("UPDATE properties SET status=%s WHERE id=%s", (new_status, prop_id))
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback()
        print("STATUS UPDATE ERROR:", e)
        return {"ok": False, "msg": str(e)}
    finally:
        cur.close()
        db.close()

@app.route("/property/mark_sold/<int:prop_id>", methods=["POST"])
def property_mark_sold(prop_id):
    res = _property_status_update(prop_id, "sold")
    if res["ok"]:
        flash("✅ Property marked as Sold", "success")
    else:
        flash(res["msg"], "danger")
    return redirect(request.referrer or "/personal/properties")

@app.route("/property/mark_rented/<int:prop_id>", methods=["POST"])
def property_mark_rented(prop_id):
    res = _property_status_update(prop_id, "rented")
    if res["ok"]:
        flash("🟣 Property marked as Rented", "success")
    else:
        flash(res["msg"], "danger")
    return redirect(request.referrer or "/personal/properties")

@app.route("/property/deactivate/<int:prop_id>", methods=["POST"])
def property_deactivate(prop_id):
    res = _property_status_update(prop_id, "inactive")
    if res["ok"]:
        flash("⏸️ Property deactivated", "success")
    else:
        flash(res["msg"], "danger")
    return redirect(request.referrer or "/personal/properties")

@app.route("/property/reactivate/<int:prop_id>", methods=["POST"])
def property_reactivate(prop_id):
    res = _property_status_update(prop_id, "active")
    if res["ok"]:
        flash("✅ Property reactivated", "success")
    else:
        flash(res["msg"], "danger")
    return redirect(request.referrer or "/personal/properties")

@app.route("/property/delete/<int:prop_id>", methods=["POST"])
def property_delete(prop_id):
    role = session.get("role")
    if role not in ("personal", "business"):
        flash("Unauthorized", "danger")
        return redirect("/login")
    db = get_db()
    cur = db.cursor(dictionary=True)
    try:
        cur.execute("SELECT user_id FROM properties WHERE id=%s", (prop_id,))
        row = cur.fetchone()
        if not row or row["user_id"] != session["user_id"]:
            flash("Not your property", "danger")
        else:
            cur.execute("DELETE FROM properties WHERE id=%s", (prop_id,))
            db.commit()
            flash("🗑️ Property deleted permanently", "success")
    except Exception as e:
        db.rollback()
        flash("Something went wrong", "danger")
    finally:
        cur.close()
        db.close()
    return redirect(request.referrer or "/personal/properties")


# PERSONAL: All Materials Page
@app.route("/personal/materials")
def personal_materials():  # Unique function name
    if session.get("role") != "personal":
        return redirect("/login")

    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute("""
        SELECT materials.*, users.business_name, users.phone AS user_phone
        FROM materials
        LEFT JOIN users ON materials.user_id = users.id
        ORDER BY materials.id DESC
    """)
    materials = cur.fetchall()
    cur.close()
    db.close()

    return render_template("personal/materials.html", materials=materials)
# offer section
@app.route("/offers")
def user_offers():

    if not session.get("user_id"):
        return redirect("/login")

    user_id = session.get("user_id")

    db = get_db()
    cur = db.cursor(dictionary=True)

    # total users
    cur.execute("SELECT COUNT(*) AS total_users FROM users")
    total_users = cur.fetchone()["total_users"] or 1

    # -------------------------
    # ACTIVE OFFERS
    # -------------------------
    cur.execute("""
    SELECT 
        o.*, 
        COUNT(e.id) AS total_enrolled,

        u1.name AS winner1_name,
        u2.name AS winner2_name,
        u3.name AS winner3_name

    FROM offers o
    LEFT JOIN offer_enrollments e ON o.id = e.offer_id
    LEFT JOIN users u1 ON o.winner_1 = u1.id
    LEFT JOIN users u2 ON o.winner_2 = u2.id
    LEFT JOIN users u3 ON o.winner_3 = u3.id

    WHERE o.expiry >= NOW()

    GROUP BY o.id
    ORDER BY o.expiry ASC
    """)
    active_offers = cur.fetchall()

    # -------------------------
    # HISTORY OFFERS
    # -------------------------
    cur.execute("""
SELECT 
    o.*, 
    COUNT(e.id) AS total_enrolled,

    u1.name AS winner1_name,
    u2.name AS winner2_name,
    u3.name AS winner3_name

FROM offers o
INNER JOIN offer_enrollments e 
    ON o.id = e.offer_id 
    AND e.user_id = %s

LEFT JOIN users u1 ON o.winner_1 = u1.id
LEFT JOIN users u2 ON o.winner_2 = u2.id
LEFT JOIN users u3 ON o.winner_3 = u3.id

WHERE o.expiry < NOW()

GROUP BY o.id
ORDER BY o.expiry DESC
""", (user_id,))

    history_offers = cur.fetchall()

    # -------------------------
    # SAFE PROCESS FUNCTION
    # -------------------------
    def process(offers_list):
        for offer in offers_list:

            enrolled_count = offer.get("total_enrolled") or 0

            offer["total_users"] = total_users

            # avoid division error
            offer["progress"] = int((enrolled_count * 100) / total_users) if total_users else 0

            left = total_users - enrolled_count
            offer["spots_left"] = left if left > 0 else 0

            # 🔧 FIX: template compares offer.days_left > 7 for the countdown
            # badge color, but this was never being calculated — that made
            # every /offers page load crash with a TypeError (Jinja's
            # Undefined has no ">" comparison), showing as Internal Server
            # Error. Compute it here so the template always has a real number.
            expiry = offer.get("expiry")
            if expiry:
                delta = expiry - datetime.now()
                offer["days_left"] = max(delta.days, 0)
            else:
                offer["days_left"] = 0

    process(active_offers)
    process(history_offers)

    # -------------------------
    # ENROLLED OFFERS
    # -------------------------
    cur.execute("""
        SELECT offer_id FROM offer_enrollments 
        WHERE user_id = %s
    """, (user_id,))
    
    enrolled = [row["offer_id"] for row in cur.fetchall()]

    # 🔒 ONE OFFER AT A TIME: check if the user is currently locked into a
    # different active offer, so the template can show this proactively.
    cur.execute("""
        SELECT o.id, o.title
        FROM offer_enrollments oe
        JOIN offers o ON oe.offer_id = o.id
        WHERE oe.user_id = %s AND o.expiry >= NOW()
        LIMIT 1
    """, (user_id,))
    active_enrollment = cur.fetchone()

    # 🔒 PLAN CHECK: pass eligibility to the template too, so the Join
    # button can show the right state without waiting for a failed click.
    cur.execute("SELECT plan_name, plan_expiry FROM users WHERE id = %s", (user_id,))
    plan_row = cur.fetchone() or {}
    raw_expiry = plan_row.get("plan_expiry")
    expiry_dt = None
    if raw_expiry:
        if isinstance(raw_expiry, str):
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    expiry_dt = datetime.strptime(raw_expiry, fmt)
                    break
                except ValueError:
                    continue
        else:
            expiry_dt = raw_expiry
    plan_active = bool(plan_row.get("plan_name") and expiry_dt and expiry_dt >= datetime.now())

    return render_template(
        "personal/offers.html",
        offers=active_offers,
        history_offers=history_offers,
        enrolled=enrolled,
        active_enrollment=active_enrollment,
        plan_active=plan_active
    )

@app.route("/join_offer/<int:offer_id>", methods=["POST"])
@app.route("/personal/join_offer/<int:offer_id>", methods=["POST"])
@app.route("/offer/enroll/<int:offer_id>", methods=["POST"])
def enroll_offer(offer_id):

    print("JOIN CLICKED", offer_id)  # DEBUG

    if not session.get("user_id"):
        return redirect("/login")

    user_id = session.get("user_id")

    db = get_db()
    cur = db.cursor(dictionary=True)

    try:
        # Offer check
        cur.execute("SELECT id FROM offers WHERE id = %s", (offer_id,))
        offer = cur.fetchone()

        if not offer:
            flash("Offer not found", "danger")
            return redirect(url_for("user_offers"))

        # 🔒 ELIGIBILITY: user must have an active Recharge Plan to join
        cur.execute("SELECT plan_name, plan_expiry FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()

        if not user:
            flash("User not found", "danger")
            return redirect(url_for("user_offers"))

        plan_name = user.get("plan_name")
        raw_expiry = user.get("plan_expiry")

        # 🔧 ROBUSTNESS FIX: some MySQL connector configs return DATETIME
        # columns as strings instead of datetime objects, which made the
        # ">=" comparison below either crash or silently compare wrong types.
        expiry_dt = None
        if raw_expiry:
            if isinstance(raw_expiry, str):
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                    try:
                        expiry_dt = datetime.strptime(raw_expiry, fmt)
                        break
                    except ValueError:
                        continue
            else:
                expiry_dt = raw_expiry

        plan_active = bool(plan_name and expiry_dt and expiry_dt >= datetime.now())

        if not plan_active:
            flash("🔒 You need an active Recharge Plan (₹499 or ₹999) to join this offer. Buy a plan from Wallet & Plans.", "danger")
            return redirect(url_for("user_offers"))

        # Already enrolled in THIS offer
        cur.execute("""
            SELECT id FROM offer_enrollments 
            WHERE user_id = %s AND offer_id = %s
        """, (user_id, offer_id))

        if cur.fetchone():
            flash("Already joined this offer", "warning")
            return redirect(url_for("user_offers"))

        # 🔒 ONE OFFER AT A TIME: a user can only be enrolled in a single
        # active (not-yet-expired) offer at once. If they're already in a
        # different offer that hasn't expired yet, block joining a new one.
        cur.execute("""
            SELECT o.id, o.title
            FROM offer_enrollments oe
            JOIN offers o ON oe.offer_id = o.id
            WHERE oe.user_id = %s AND o.expiry >= NOW()
            LIMIT 1
        """, (user_id,))
        active_enrollment = cur.fetchone()

        if active_enrollment:
            flash(f"⚠️ You're already in an active offer ({active_enrollment['title']}). You can only join one offer at a time — wait for it to end first.", "danger")
            return redirect(url_for("user_offers"))

        # Insert enrollment
        cur.execute("""
            INSERT INTO offer_enrollments (user_id, offer_id)
            VALUES (%s, %s)
        """, (user_id, offer_id))

        db.commit()

        flash("Successfully joined offer!", "success")
        return redirect(url_for("user_offers"))

    except Exception as e:
        db.rollback()
        print("ERROR:", e)
        flash("Something went wrong", "danger")
        return redirect(url_for("user_offers"))
# ==============================
# 🔹 PROFILE ROUTE (FIXED)
# ==============================
@app.route("/personal/profile")
def personal_profile():

    if not session.get("user_id") or session.get("role") != "personal":
        return redirect("/login")

    db = get_db()
    cur = db.cursor(dictionary=True)

    try:
        # 👤 USER INFO
        cur.execute("""
            SELECT id, name, email, phone, address, wallet_balance, profile_pic
            FROM users
            WHERE id = %s
        """, (session["user_id"],))
        user = cur.fetchone()

        if not user:
            flash("User not found", "danger")
            return redirect("/login")

        # ✅ SAFE NAME
        if not user.get("name"):
            user["name"] = "User"

        # ✅ IMPORTANT FIX:
        # DO NOT modify path here, only keep filename
        if not user.get("profile_pic"):
            user["profile_pic"] = None

        # 🏠 PROPERTIES
        cur.execute("""
            SELECT id, title, price, location, flat_type, created_at, image1
            FROM properties
            WHERE user_id = %s
            ORDER BY id DESC
        """, (session["user_id"],))
        properties = cur.fetchall()

        # 🧱 MATERIALS
        cur.execute("""
            SELECT id, title, price, quantity
            FROM materials
            WHERE user_id = %s
            ORDER BY id DESC
        """, (session["user_id"],))
        materials = cur.fetchall()

    except Exception as e:
        print("PROFILE ERROR:", e)
        flash("Something went wrong!", "danger")
        return redirect("/login")

    finally:
        cur.close()
        db.close()

    return render_template(
        "personal/profile.html",
        user=user,
        properties=properties,
        materials=materials
    )


# ==============================
# 🔹 UPLOAD PROFILE PIC (FIXED)
# ==============================
UPLOAD_FOLDER = "static/uploads"

@app.route("/personal/update-profile-pic", methods=["POST"])
def update_profile_pic():

    if not session.get("user_id"):
        return redirect("/login")

    file = request.files.get("profile_pic")

    if file and file.filename != "":

        # safe filename
        filename = secure_filename(file.filename)

        # unique filename
        filename = f"{session['user_id']}_{filename}"

        # ensure folder exists
        if not os.path.exists(UPLOAD_FOLDER):
            os.makedirs(UPLOAD_FOLDER)

        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)

        db = get_db()
        cur = db.cursor()

        cur.execute("""
            UPDATE users
            SET profile_pic = %s
            WHERE id = %s
        """, (filename, session["user_id"]))

        db.commit()
        cur.close()
        db.close()

    return redirect("/personal/profile")

# ─────────────────────────────────────────────
# UPDATE PROFILE ROUTE
# ─────────────────────────────────────────────
@app.route("/personal/update-profile", methods=["POST"])
def update_profile():

    if not session.get("user_id"):
        return redirect("/login")

    user_id = session.get("user_id")

    name = request.form.get("name")
    email = request.form.get("email")
    old_email = request.form.get("old_email")
    phone = request.form.get("phone")
    address = request.form.get("address")
    new_password = request.form.get("new_password")

    db = get_db()
    cur = db.cursor(dictionary=True)

    try:
        # ── 1. DUPLICATE EMAIL CHECK ──
        if email != old_email:
            cur.execute("""
                SELECT id FROM users 
                WHERE email = %s AND id != %s
            """, (email, user_id))

            existing = cur.fetchone()
            cur.fetchall()  # 🔥 fix unread result

            if existing:
                flash("Email already registered!", "danger")
                return redirect("/personal/profile")

        # ── 2. OTP REQUIRED ──
        if email != old_email or (new_password and new_password.strip()):

            otp = str(random.randint(100000, 999999))

            session["otp"] = otp
            session["new_email"] = email

            session["temp_data"] = {
                "name": name,
                "phone": phone,
                "address": address
            }

            if new_password and new_password.strip():
                session["temp_data"]["password"] = new_password

            print("OTP:", otp)

            # ── SEND EMAIL ──
            msg = Message(
                subject="OTP Verification",
                recipients=[email]
            )

            msg.body = f"""
Hello,

Your OTP for profile update is:

{otp}

Valid for 5 minutes.
"""

            send_mail(msg)

            return redirect("/verify-email-otp")

        # ── 3. NORMAL UPDATE ──
        cur.execute("""
            UPDATE users 
            SET name=%s, phone=%s, address=%s
            WHERE id=%s
        """, (name, phone, address, user_id))

        db.commit()

        flash("Profile updated successfully!", "success")
        return redirect("/personal/profile")

    except Exception as e:
        db.rollback()
        print("ERROR:", e)
        flash("Something went wrong!", "danger")
        return redirect("/personal/profile")

    finally:
        cur.close()
        db.close()


# ─────────────────────────────────────────────
# VERIFY OTP ROUTE
# ─────────────────────────────────────────────
@app.route("/verify-email-otp", methods=["GET", "POST"])
def verify_email_otp():

    if not session.get("user_id"):
        return redirect("/login")

    if request.method == "POST":

        user_otp = request.form.get("otp")
        session_otp = session.get("otp")

        if user_otp != session_otp:
            flash("Invalid OTP", "danger")
            return redirect("/verify-email-otp")

        data = session.get("temp_data")
        new_email = session.get("new_email")
        user_id = session.get("user_id")

        if not data:
            flash("Session expired!", "danger")
            return redirect("/personal/profile")

        db = get_db()
        cur = db.cursor(dictionary=True)

        try:
            # ── 1. FINAL EMAIL CHECK (important) ──
            cur.execute("""
                SELECT id FROM users 
                WHERE email = %s AND id != %s
            """, (new_email, user_id))

            existing = cur.fetchone()
            cur.fetchall()  # 🔥 fix unread result

            if existing:
                flash("Email already taken!", "danger")
                return redirect("/personal/profile")

            # ── 2. UPDATE DATA ──
            query = """
                UPDATE users 
                SET name=%s, email=%s, phone=%s, address=%s
            """

            values = [
                data["name"],
                new_email,
                data["phone"],
                data["address"]
            ]

            # password update
            if "password" in data:
                query += ", password=%s"
                values.append(generate_password_hash(data["password"]))

            query += " WHERE id=%s"
            values.append(user_id)

            cur.execute(query, tuple(values))
            db.commit()

            # ── 3. CLEAR SESSION ──
            session.pop("otp", None)
            session.pop("new_email", None)
            session.pop("temp_data", None)

            flash("Profile updated successfully!", "success")
            return redirect("/personal/profile")

        except Exception as e:
            db.rollback()
            print("ERROR:", e)
            flash("Database error!", "danger")
            return redirect("/personal/profile")

        finally:
            cur.close()
            db.close()

    return render_template("verify_otp.html")
SERVICE_CATEGORIES = [
    "Plumber", "Electrician", "Carpenter", "Painter", "Fabricator",
    "Welder", "Engineer", "Construction Contractor", "Packers & Movers", "Other"
]
SERVICE_EXPERIENCE = ["0-1 Year", "1-3 Years", "3-5 Years", "5+ Years"]

GOVT_ID_TYPES = ["Aadhaar Card", "PAN Card", "Driving License", "Voter ID", "Passport"]

# Grouped per your spec: Skilled Workers / Engineers / Contractors & Businesses
PROFESSIONAL_PROOF_GROUPS = {
    "Skilled Worker": ["PMKVY / Kaushal Vikas Certificate", "ITI Certificate", "Apprenticeship Certificate"],
    "Engineer": ["Diploma Certificate", "B.E. / B.Tech Degree Certificate"],
    "Contractor / Business": ["Shop Act License", "GST Registration Certificate", "Udyam Registration Certificate", "Company Registration Certificate"],
}
PROFESSIONAL_PROOF_TYPES = [doc for group in PROFESSIONAL_PROOF_GROUPS.values() for doc in group]


@app.route("/register-service-provider", methods=["GET", "POST"])
def register_service_provider():
    try:
        return _register_service_provider_impl()
    except Exception as e:
        tb = traceback.format_exc()
        print("=== REGISTER SERVICE PROVIDER CRASH ===")
        print(tb)
        # TEMPORARY: show the real traceback in the browser so we can see
        # exactly what's failing on PythonAnywhere. Remove this once fixed.
        return f"<pre style='white-space:pre-wrap;font-size:13px;padding:20px;'>{tb}</pre>", 500


def _ensure_service_providers_table(db):
    """🛡️ Shared self-healing safety net: creates service_providers (and any
    newer columns it needs) if they're somehow still missing on the live DB —
    e.g. an earlier deploy's migration failed partway. Used by every route
    that touches this table (register, browse, admin) so none of them can
    crash with 'table/column doesn't exist' -> Internal Server Error."""
    try:
        heal_cur = db.cursor()
        heal_cur.execute("""
            CREATE TABLE IF NOT EXISTS service_providers (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                category VARCHAR(50) NOT NULL,
                experience VARCHAR(20) NOT NULL,
                about TEXT,
                id_doc1_type VARCHAR(50),
                id_doc1 VARCHAR(255),
                id_doc2_type VARCHAR(50),
                id_doc2 VARCHAR(255),
                professional_doc_type VARCHAR(80),
                professional_doc VARCHAR(255),
                status VARCHAR(20) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.commit()
        for col, ddl in [
            ("id_doc1_type", "VARCHAR(50)"), ("id_doc2_type", "VARCHAR(50)"),
            ("professional_doc_type", "VARCHAR(80)"),
        ]:
            _ensure_column(heal_cur, "service_providers", col, ddl)
            db.commit()
        heal_cur.close()
    except Exception as e:
        db.rollback()
        print("SERVICE PROVIDERS TABLE SELF-HEAL ERROR:", e)


def _register_service_provider_impl():
    if not session.get("user_id"):
        return redirect("/login")

    user_id = session["user_id"]
    db = get_db()
    cur = db.cursor(dictionary=True)

    _ensure_service_providers_table(db)

    # 👤 Auto-filled profile info — always sourced from the users table,
    # never editable on this form.
    cur.execute("""
        SELECT id, name, business_name, email, phone, address, profile_pic
        FROM users WHERE id=%s
    """, (user_id,))
    user = cur.fetchone()

    if not user:
        cur.close(); db.close()
        flash("User not found", "danger")
        return redirect("/login")

    if request.method == "POST":
        category = request.form.get("category")
        experience = request.form.get("experience")
        about = request.form.get("about")

        if category not in SERVICE_CATEGORIES or experience not in SERVICE_EXPERIENCE or not about:
            flash("Please fill Service Category, Experience and About Your Service.", "danger")
            cur.close(); db.close()
            return redirect("/register-service-provider")

        # 🪪 Verification documents — type + file for each
        id_doc1_type = request.form.get("id_doc1_type")
        id_doc2_type = request.form.get("id_doc2_type")
        professional_doc_type = request.form.get("professional_doc_type")

        id_doc1 = request.files.get("id_doc1")
        id_doc2 = request.files.get("id_doc2")
        professional_doc = request.files.get("professional_doc")

        if id_doc1_type not in GOVT_ID_TYPES or id_doc2_type not in GOVT_ID_TYPES:
            flash("Please select which 2 Government ID documents you're uploading.", "danger")
            cur.close(); db.close()
            return redirect("/register-service-provider")

        if id_doc1_type == id_doc2_type:
            flash("Please choose two DIFFERENT Government ID documents (e.g. Aadhaar + PAN).", "danger")
            cur.close(); db.close()
            return redirect("/register-service-provider")

        if professional_doc_type not in PROFESSIONAL_PROOF_TYPES:
            flash("Please select your Professional Proof document type.", "danger")
            cur.close(); db.close()
            return redirect("/register-service-provider")

        if not (id_doc1 and id_doc1.filename) or not (id_doc2 and id_doc2.filename) or not (professional_doc and professional_doc.filename):
            flash("Please upload both Government ID documents and 1 Professional Proof document.", "danger")
            cur.close(); db.close()
            return redirect("/register-service-provider")

        upload_path = "static/uploads"
        os.makedirs(upload_path, exist_ok=True)

        def save_doc(file, tag):
            fname = f"sp_{user_id}_{tag}_{int(time.time())}_" + secure_filename(file.filename)
            file.save(os.path.join(upload_path, fname))
            return fname

        f_doc1 = save_doc(id_doc1, "id1")
        f_doc2 = save_doc(id_doc2, "id2")
        f_prof = save_doc(professional_doc, "prof")

        try:
            cur.execute("""
                INSERT INTO service_providers
                (user_id, category, experience, about,
                 id_doc1_type, id_doc1, id_doc2_type, id_doc2,
                 professional_doc_type, professional_doc, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending')
            """, (
                user_id, category, experience, about,
                id_doc1_type, f_doc1, id_doc2_type, f_doc2,
                professional_doc_type, f_prof
            ))
            db.commit()
            flash("✅ Application submitted! We'll review your documents and get back to you.", "success")
        except Exception as e:
            db.rollback()
            print("SERVICE PROVIDER REGISTER ERROR:", e)
            flash("Something went wrong. Please try again.", "danger")
        finally:
            cur.close()
            db.close()

        return redirect("/register-service-provider")

    # GET — show existing application status if any
    try:
        cur.execute("""
            SELECT * FROM service_providers WHERE user_id=%s ORDER BY id DESC LIMIT 1
        """, (user_id,))
        existing = cur.fetchone()
    except Exception as e:
        print("SERVICE PROVIDER EXISTING LOOKUP ERROR:", e)
        existing = None
    cur.close()
    db.close()

    return render_template(
        "service_provider_register.html",
        user=user,
        existing=existing,
        categories=SERVICE_CATEGORIES,
        experience_options=SERVICE_EXPERIENCE,
        govt_id_types=GOVT_ID_TYPES,
        proof_groups=PROFESSIONAL_PROOF_GROUPS
    )


# ==============================
# 🔹 ADMIN — SERVICE PROVIDER APPLICATIONS
# ==============================
@app.route("/admin/service_providers")
def admin_service_providers():
    if session.get("role") != "admin":
        return redirect("/login")

    db = get_db()
    _ensure_service_providers_table(db)
    cur = db.cursor(dictionary=True)
    cur.execute("""
        SELECT sp.*, u.name AS user_name, u.business_name, u.email, u.phone
        FROM service_providers sp
        LEFT JOIN users u ON sp.user_id = u.id
        ORDER BY sp.id DESC
    """)
    applications = cur.fetchall()
    cur.close()
    db.close()

    return render_template("admin/service_providers.html", applications=applications)


@app.route("/admin/service_providers/<int:sp_id>/approve", methods=["POST"])
def admin_service_provider_approve(sp_id):
    if session.get("role") != "admin":
        return redirect("/login")

    db = get_db()
    cur = db.cursor()
    cur.execute("UPDATE service_providers SET status='approved' WHERE id=%s", (sp_id,))
    db.commit()
    cur.close()
    db.close()
    flash("✅ Service Provider application approved.", "success")
    return redirect("/admin/service_providers")


@app.route("/admin/service_providers/<int:sp_id>/reject", methods=["POST"])
def admin_service_provider_reject(sp_id):
    if session.get("role") != "admin":
        return redirect("/login")

    db = get_db()
    cur = db.cursor()
    cur.execute("UPDATE service_providers SET status='rejected' WHERE id=%s", (sp_id,))
    db.commit()
    cur.close()
    db.close()
    flash("❌ Service Provider application rejected.", "success")
    return redirect("/admin/service_providers")


@app.route("/admin/service_providers/<int:sp_id>/delete", methods=["POST"])
def admin_service_provider_delete(sp_id):
    if session.get("role") != "admin":
        return redirect("/login")

    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM service_providers WHERE id=%s", (sp_id,))
    db.commit()
    cur.close()
    db.close()
    flash("🗑️ Application deleted.", "success")
    return redirect("/admin/service_providers")


@app.route("/services")
def browse_services():
    if not session.get("user_id"):
        return redirect("/login")

    category = request.args.get("category", "").strip()

    db = get_db()
    _ensure_service_providers_table(db)
    cur = db.cursor(dictionary=True)

    if category:
        cur.execute("""
            SELECT sp.*, u.name AS user_name, u.business_name, u.phone, u.profile_pic
            FROM service_providers sp
            LEFT JOIN users u ON sp.user_id = u.id
            WHERE sp.status = 'approved' AND sp.category = %s
            ORDER BY sp.id DESC
        """, (category,))
    else:
        cur.execute("""
            SELECT sp.*, u.name AS user_name, u.business_name, u.phone, u.profile_pic
            FROM service_providers sp
            LEFT JOIN users u ON sp.user_id = u.id
            WHERE sp.status = 'approved'
            ORDER BY sp.id DESC
        """)
    providers = cur.fetchall()
    cur.close()
    db.close()

    return render_template(
        "services_browse.html",
        providers=providers,
        selected_category=category,
        categories=SERVICE_CATEGORIES
    )


@app.context_processor
def inject_user():
    if "user_id" in session:
        db = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute("SELECT name, profile_pic FROM users WHERE id=%s", (session["user_id"],))
        user = cur.fetchone()
        cur.close()
        db.close()
        return dict(user=user)
    return dict(user=None)
@app.route("/logout")
def logout():
    # Remove specific session data
    session.pop("user_id", None)
    session.pop("role", None)

    # Clear full session (extra safety)
    session.clear()

    # Redirect to login page
    return redirect("/login")
if __name__ == "__main__":
    app.run(debug=True)
