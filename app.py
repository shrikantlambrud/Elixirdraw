from flask import (
    Flask, render_template, request,
    redirect, session, url_for, flash
)

from flask_mail import Mail, Message

import mysql.connector
import random
import os

from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
app = Flask(__name__)
app.secret_key = "secret123"


# DATABASE CONNECTION
def get_db():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="Shrikant",
        database="realestate"
    )

# ---------------- MAIL CONFIG ----------------

app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'elixirdraw26@gmail.com'
app.config['MAIL_PASSWORD'] = 'fevs ausn pxdx fmjf'
app.config['MAIL_DEFAULT_SENDER'] = 'elixirdraw26@gmail.com'

mail = Mail(app)
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


@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":
        name = request.form['name']
        email = request.form['email']
        phone = request.form['phone']
        address = request.form['address']

        # 🔐 HASH PASSWORD
        password = generate_password_hash(request.form['password'])

        # 🔢 OTP GENERATE
        otp = str(random.randint(100000, 999999))

        # ⏱ OTP EXPIRY (10 minutes)
        otp_expiry = (datetime.now() + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")

        # 💾 STORE TEMP DATA IN SESSION
        session['temp_user'] = {
            "name": name,
            "email": email,
            "phone": phone,
            "address": address,
            "password": password,
            "otp": otp,
            "otp_expiry": otp_expiry
        }

        # 📩 SEND EMAIL
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

        mail.send(msg)

        flash("OTP sent to your email successfully!", "success")

        return redirect(url_for("verify_otp"))

    return render_template("register.html")
@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():

    if request.method == "POST":
        user_otp = request.form['otp']
        temp_user = session.get('temp_user')

        if not temp_user:
            return "Session expired. Register again."

        if user_otp == temp_user.get('otp'):

            # DB connection
            db = get_db()
            cursor = db.cursor()

            # CHECK duplicate email
            cursor.execute("SELECT * FROM users WHERE email=%s", (temp_user['email'],))
            if cursor.fetchone():
                return "Email already exists"

            # INSERT USER (IMPORTANT: role='personal')
            cursor.execute("""
                INSERT INTO users (name, email, phone, address, password, role)
                VALUES (%s,%s,%s,%s,%s,'personal')
            """, (
                temp_user['name'],
                temp_user['email'],
                temp_user['phone'],
                temp_user['address'],
                temp_user['password']
            ))

            db.commit()

            session.pop('temp_user', None)

            return redirect("/login")

        else:
            return "Invalid OTP"

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

        mail.send(msg)

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
        mail.send(msg)

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

    cur.execute("""
        UPDATE users
        SET wallet_balance = wallet_balance + %s
        WHERE id=%s
    """, (req["amount"], req["user_id"]))

    cur.execute("""
        UPDATE wallet_requests
        SET status='approved'
        WHERE id=%s
    """, (id,))

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
        FROM user
        WHERE role='business'
    """)
    result = cur.fetchone()
    total_amount = result["total_amount"] if result["total_amount"] else 0

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

        # IMAGE UPLOAD
        image = request.files["image1"]
        filename = ""

        if image and image.filename != "":
            filename = image.filename
            image.save("static/uploads/" + filename)

        db = get_db()
        cur = db.cursor()

        # CHECK WALLET BALANCE
        cur.execute(
        "SELECT wallet_balance FROM users WHERE id=%s",
        (session["user_id"],)
        )

        wallet = cur.fetchone()[0]

        if wallet < 10:
            return "Insufficient wallet balance"

        # DEDUCT WALLET ₹10
        cur.execute(
        "UPDATE users SET wallet_balance = wallet_balance - 10 WHERE id=%s",
        (session["user_id"],)
        )

        # INSERT PROPERTY
        cur.execute("""
        INSERT INTO properties
        (user_id,title,price,location,property_type,flat_type,plot_size,map_link,image1,description)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
        filename,
        description
        ))

        db.commit()

        return redirect("/business/dashboard")

    return render_template("business/add_property.html")

# ADD MATERIAL
@app.route("/business/add_material", methods=["GET","POST"])
def add_material():

    if session.get("role") != "business":
        return redirect("/login")

    if request.method == "POST":

        title = request.form["title"]
        price = request.form["price"]
        quantity = request.form.get("quantity")
        location = request.form["location"]
        map_link = request.form.get("map_link")
        description = request.form["description"]

        # IMAGE UPLOAD
        file = request.files["image1"]
        filename = ""

        if file and file.filename != "":
            filename = file.filename
            file.save("static/uploads/" + filename)

        db = get_db()
        cur = db.cursor()

        cur.execute("""
        INSERT INTO materials
        (user_id,title,price,quantity,location,map_link,image1,description)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (session["user_id"],title,price,quantity,location,map_link,filename,description))

        db.commit()

        return redirect("/business/dashboard")

    return render_template("business/add_material.html")

@app.route("/business/wallet")
def business_wallet():

    if session.get("role") != "business":
        return redirect("/login")

    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute("SELECT wallet_balance FROM users WHERE id=%s",(session["user_id"],))
    wallet = cur.fetchone()

    cur.execute("""
    SELECT * FROM wallet_requests
    WHERE user_id=%s
    ORDER BY id DESC
    """,(session["user_id"],))

    requests = cur.fetchall()

    return render_template(
        "business/wallet.html",
        wallet=wallet,
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
# business profile
@app.route("/business/profile")
def business_profile():
    # Assuming you store logged-in business user ID in session
    business_id = session.get("business_id")
    if not business_id:
        return redirect("/login")  # redirect if not logged in

    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT * FROM user WHERE id=%s AND role='business'", (business_id,))
    business = cur.fetchone()

    return render_template("business/profile.html", business=business)
# personal dashboard
@app.route("/personal/dashboard")
def personal_dashboard():

    if not session.get("user_id") or session.get("role") != "personal":
        return redirect("/login")

    db = get_db()
    cur = db.cursor(dictionary=True)

    # 🏠 PROPERTIES (IMPORTANT FIX: phone mapped as user_phone)
    cur.execute("""
        SELECT 
            properties.*,
            users.business_name,
            users.phone AS user_phone
        FROM properties
        LEFT JOIN users ON properties.user_id = users.id
        ORDER BY properties.id DESC
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
        materials=materials
    )


@app.route("/personal/add_rent", methods=["GET", "POST"])
def personal_add_rent():

    if not session.get("user_id") or session.get("role") != "personal":
        return redirect("/login")

    if request.method == "POST":

        title = request.form.get("title")
        property_type = request.form.get("flat_type")   # room / flat
        price = request.form.get("price")
        city = request.form.get("city")
        area = request.form.get("area")
        description = request.form.get("description")

        location = f"{city} - {area}"

        # 📸 IMAGE UPLOAD
        file = request.files.get("image1")
        filename = None

        if file and file.filename:
            filename = file.filename
            file.save("static/uploads/" + filename)

        db = get_db()
        cur = db.cursor(dictionary=True)

        # 💰 WALLET CHECK
        cur.execute(
            "SELECT wallet_balance FROM users WHERE id=%s",
            (session["user_id"],)
        )
        wallet_row = cur.fetchone()
        wallet = wallet_row["wallet_balance"] if wallet_row else 0

        if wallet < 10:
            flash("❌ Insufficient wallet balance")
            return redirect("/personal/wallet")

        # 💸 WALLET DEDUCT
        cur.execute("""
            UPDATE users
            SET wallet_balance = wallet_balance - 10
            WHERE id=%s
        """, (session["user_id"],))

        # 🏠 INSERT PROPERTY (NO contact_number needed)
        cur.execute("""
            INSERT INTO properties
            (user_id, title, price, location, property_type, image1, description)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            session["user_id"],
            title,
            price,
            location,
            property_type,
            filename,
            description
        ))

        db.commit()

        flash("✅ Rent added successfully!")
        return redirect("/personal/dashboard")

    return render_template("personal/add_rent.html")
@app.route("/personal/wallet")
def personal_wallet():

    if session.get("role") != "personal":
        return redirect("/login")

    db = get_db()
    cur = db.cursor(dictionary=True)

    # wallet balance
    cur.execute("SELECT wallet_balance FROM users WHERE id=%s", (session["user_id"],))
    wallet = cur.fetchone()

    # request history
    cur.execute("""
        SELECT * FROM wallet_requests
        WHERE user_id=%s
        ORDER BY id DESC
    """, (session["user_id"],))

    requests = cur.fetchall()

    return render_template(
        "personal/wallet.html",
        wallet=wallet,
        requests=requests
    )
@app.route("/personal/add_wallet", methods=["POST"])
def personal_add_wallet():

    if session.get("role") != "personal":
        return redirect("/login")

    amount = request.form["amount"]
    utr = request.form["utr"]

    file = request.files["screenshot"]
    filename = ""

    if file and file.filename != "":
        filename = file.filename
        file.save("static/uploads/" + filename)

    db = get_db()
    cur = db.cursor()

    cur.execute("""
        INSERT INTO wallet_requests
        (user_id, amount, utr_number, payment_screenshot)
        VALUES (%s,%s,%s,%s)
    """, (session["user_id"], amount, utr, filename))

    db.commit()

    return redirect("/personal/wallet")
# PERSONAL: All Properties Page
@app.route("/personal/properties")
def personal_properties():  # Unique function name
    if session.get("role") != "personal":
        return redirect("/login")

    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute("""
        SELECT properties.*, users.business_name, users.phone
        FROM properties
        LEFT JOIN users ON properties.user_id = users.id
    """)
    properties = cur.fetchall()

    return render_template("personal/properties.html", properties=properties)


# PERSONAL: All Materials Page
@app.route("/personal/materials")
def personal_materials():  # Unique function name
    if session.get("role") != "personal":
        return redirect("/login")

    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute("""
        SELECT materials.*, users.business_name, users.phone
        FROM materials
        LEFT JOIN users ON materials.user_id = users.id
    """)
    materials = cur.fetchall()

    return render_template("personal/materials.html", materials=materials)
@app.route("/personal/profile")
def personal_profile():

    # 🔐 Auth Check
    if not session.get("user_id") or session.get("role") != "personal":
        return redirect("/login")

    db = get_db()
    cur = db.cursor(dictionary=True)

    try:
        # 👤 USER INFO
        cur.execute("""
            SELECT id, name, email, phone, address, wallet_balance
            FROM users
            WHERE id = %s
        """, (session["user_id"],))
        user = cur.fetchone()

        # ❌ अगर user नाही सापडला
        if not user:
            flash("User not found", "danger")
            return redirect("/login")

        # 🏠 PROPERTY HISTORY
        cur.execute("""
            SELECT id, title, price, location, flat_type, created_at, image1
            FROM properties
            WHERE user_id = %s
            ORDER BY id DESC
        """, (session["user_id"],))
        properties = cur.fetchall()

        # 🧱 MATERIAL HISTORY
        cur.execute("""
    SELECT id, title, price, quantity
    FROM materials
    WHERE user_id = %s
    ORDER BY id DESC
""", (session["user_id"],))
        materials = cur.fetchall()

    finally:
        cur.close()
        db.close()

    return render_template(
        "personal/profile.html",
        user=user,
        properties=properties,
        materials=materials
    )

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
