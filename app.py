import os
from datetime import datetime
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from flask import Flask, flash, redirect, render_template, request, send_file, url_for, send_from_directory
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, or_
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from openpyxl import Workbook, load_workbook

# --- CONFIGURAZIONE ---
db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "login"

ALLOWED_CERT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}

def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "evolve-industrial-2026")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
        "DATABASE_URL", "sqlite:///" + os.path.join(app.instance_path, "evolve.db")
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Gestione Cartelle Upload
    app.config["UPLOAD_FOLDER"] = os.path.join(app.root_path, "static", "uploads")
    app.config["CERT_FOLDER"] = os.path.join(app.config["UPLOAD_FOLDER"], "attestati")

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["CERT_FOLDER"], exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)

    with app.app_context():
        db.create_all()
        # Creazione Admin Default
        if not User.query.filter_by(username="admin").first():
            u = User(username="admin", role="admin")
            u.set_password("admin123!")
            db.session.add(u)
            db.session.commit()
        if not AppSetting.query.first():
            db.session.add(AppSetting())
            db.session.commit()

    @app.context_processor
    def inject_branding():
        return {"app_settings": AppSetting.query.first()}

    register_routes(app)
    return app

# --- MODELLI ---

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), default="admin")
    def set_password(self, raw): self.password_hash = generate_password_hash(raw)
    def check_password(self, raw): return check_password_hash(self.password_hash, raw)

class AppSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(150), default="Evolve Impianti Srls")
    logo_path = db.Column(db.String(255), default="")
    bolla_prefix = db.Column(db.String(20), default="BOL")

class Technician(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(50), default="")
    notes = db.Column(db.String(255), default="")
    certificates = db.relationship("Certificate", backref="technician", cascade="all, delete-orphan")

class Certificate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    technician_id = db.Column(db.Integer, db.ForeignKey("technician.id"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    description = db.Column(db.String(150))
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)

class WarehouseItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(80), nullable=False)
    category = db.Column(db.String(50), default="materiale")
    description = db.Column(db.String(255), nullable=False)
    serialized = db.Column(db.Boolean, default=False)
    serial = db.Column(db.String(120), default="")
    quantity = db.Column(db.Integer, default=1)
    unit = db.Column(db.String(20), default="pz")
    min_stock = db.Column(db.Integer, default=0)
    assigned_to = db.Column(db.Integer, db.ForeignKey("technician.id"))
    last_transfer_date = db.Column(db.String(40), default="")
    technician = db.relationship("Technician", backref="mobile_items")

class Tool(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(80), nullable=False)
    description = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(40), default="disponibile")
    assigned_to = db.Column(db.Integer, db.ForeignKey("technician.id"))

class Van(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plate = db.Column(db.String(30), unique=True, nullable=False)
    model = db.Column(db.String(120))
    assigned_to = db.Column(db.Integer, db.ForeignKey("technician.id"))

class Transfer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bolla_no = db.Column(db.String(40), unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    technician_id = db.Column(db.Integer, db.ForeignKey("technician.id"))
    technician = db.relationship("Technician")

# --- ROTTE ---

@login_manager.user_loader
def load_user(user_id): return User.query.get(int(user_id))

def register_routes(app):
    @app.route("/")
    def home(): return redirect(url_for("dashboard" if current_user.is_authenticated else "login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            user = User.query.filter_by(username=request.form.get("username")).first()
            if user and user.check_password(request.form.get("password")):
                login_user(user)
                return redirect(url_for("dashboard"))
        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout(): logout_user(); return redirect(url_for("login"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        stats = {"technicians": Technician.query.count(), "items": WarehouseItem.query.count()}
        low_stock = WarehouseItem.query.filter(WarehouseItem.quantity <= WarehouseItem.min_stock).all()
        return render_template("dashboard.html", stats=stats, low_stock=low_stock)

    # --- TECNICI ---
    @app.route("/technicians", methods=["GET", "POST"])
    @login_required
    def technicians():
        if request.method == "POST":
            db.session.add(Technician(name=request.form.get("name"), phone=request.form.get("phone")))
            db.session.commit()
            return redirect(url_for("technicians"))
        list_tech = Technician.query.all()
        return render_template("technicians.html", technicians=list_tech)

    @app.route("/technician/<int:tech_id>")
    @login_required
    def technician_detail(tech_id):
        tech = Technician.query.get_or_404(tech_id)
        certs = Certificate.query.filter_by(technician_id=tech.id).all()
        return render_template("technician_detail.html", tech=tech, certs=certs)

    @app.route("/technician/<int:tech_id>/upload_cert", methods=["POST"])
    @login_required
    def upload_cert(tech_id):
        file = request.files.get("cert_file")
        if file:
            filename = f"tech_{tech_id}_{uuid4().hex}{Path(file.filename).suffix}"
            file.save(os.path.join(app.config["CERT_FOLDER"], filename))
            db.session.add(Certificate(technician_id=tech_id, filename=filename, description=request.form.get("description")))
            db.session.commit()
        return redirect(url_for("technician_detail", tech_id=tech_id))

    @app.route("/certificate/view/<int:cert_id>")
    @login_required
    def view_cert(cert_id):
        cert = Certificate.query.get_or_404(cert_id)
        return send_from_directory(app.config["CERT_FOLDER"], cert.filename)

    # --- MAGAZZINO ---
    @app.route("/warehouse", methods=["GET", "POST"])
    @login_required
    def warehouse():
        if request.method == "POST":
            db.session.add(WarehouseItem(code=request.form.get("code"), description=request.form.get("description"), quantity=int(request.form.get("quantity") or 0)))
            db.session.commit()
        items = WarehouseItem.query.filter_by(assigned_to=None).all()
        return render_template("warehouse.html", items=items)

    @app.route("/tools")
    @login_required
    def tools():
        return render_template("tools.html", items=Tool.query.all())

    @app.route("/vans")
    @login_required
    def vans():
        return render_template("vans.html", items=Van.query.all())

    @app.route("/transfers")
    @login_required
    def transfers():
        return render_template("transfers.html", transfers=Transfer.query.all(), technicians=Technician.query.all())

    @app.route("/settings")
    @login_required
    def settings():
        return render_template("settings.html", settings_obj=AppSetting.query.first())

app = create_app()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
