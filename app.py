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

# Configurazione Iniziale
db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "login"

ALLOWED_LOGO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
ALLOWED_CERT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}

def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "evolve-industrial-key-2026")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
        "DATABASE_URL",
        "sqlite:///" + os.path.join(app.instance_path, "evolve.db"),
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Cartelle per caricamento file
    app.config["UPLOAD_FOLDER"] = os.path.join(app.root_path, "static", "uploads")
    app.config["CERT_FOLDER"] = os.path.join(app.config["UPLOAD_FOLDER"], "attestati")

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["CERT_FOLDER"], exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)

    with app.app_context():
        db.create_all()

        # Utente Admin di default
        if not User.query.filter_by(username="admin").first():
            u = User(username="admin", role="admin")
            u.set_password(os.getenv("DEFAULT_ADMIN_PASSWORD", "admin123!"))
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

# --- MODELLI DATABASE ---

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), default="admin", nullable=False)

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

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
    # Relazione con i certificati/corsi
    certificates = db.relationship("Certificate", backref="technician", cascade="all, delete-orphan")

class Certificate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    technician_id = db.Column(db.Integer, db.ForeignKey("technician.id"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    description = db.Column(db.String(150))
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)

class WarehouseItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(80), nullable=False) # Questo è il BARCODE
    category = db.Column(db.String(50), nullable=False, default="materiale")
    description = db.Column(db.String(255), nullable=False)
    serialized = db.Column(db.Boolean, default=False, nullable=False)
    serial = db.Column(db.String(120), index=True, default="")
    quantity = db.Column(db.Integer, default=1, nullable=False)
    unit = db.Column(db.String(20), default="pz")
    min_stock = db.Column(db.Integer, default=0)
    notes = db.Column(db.String(255), default="")
    client_default = db.Column(db.String(120), default="")
    assigned_to = db.Column(db.Integer, db.ForeignKey("technician.id"))
    last_transfer_date = db.Column(db.String(40), default="")
    last_client = db.Column(db.String(120), default="")
    last_job = db.Column(db.String(120), default="")

    technician = db.relationship("Technician", backref="mobile_items")

class Tool(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(80), nullable=False)
    serial = db.Column(db.String(120), default="")
    description = db.Column(db.String(255), nullable=False)
    charge_value = db.Column(db.Float, default=0)
    status = db.Column(db.String(40), default="disponibile")
    notes = db.Column(db.String(255), default="")
    assigned_to = db.Column(db.Integer, db.ForeignKey("technician.id"))
    technician = db.relationship("Technician", backref="tools")

class Van(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plate = db.Column(db.String(30), unique=True, nullable=False)
    model = db.Column(db.String(120), default="")
    status = db.Column(db.String(40), default="attivo")
    notes = db.Column(db.String(255), default="")
    assigned_to = db.Column(db.Integer, db.ForeignKey("technician.id"))
    technician = db.relationship("Technician", backref="vans")

class Charge(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    technician_id = db.Column(db.Integer, db.ForeignKey("technician.id"))
    description = db.Column(db.String(255), nullable=False)
    amount = db.Column(db.Float, default=0, nullable=False)
    status = db.Column(db.String(40), default="aperto")
    notes = db.Column(db.String(255), default="")
    technician = db.relationship("Technician", backref="charges")

class Transfer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bolla_no = db.Column(db.String(40), unique=True, nullable=False)
    transfer_type = db.Column(db.String(20), default="out", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    technician_id = db.Column(db.Integer, db.ForeignKey("technician.id"))
    client = db.Column(db.String(120), default="")
    job = db.Column(db.String(120), default="")
    notes = db.Column(db.String(255), default="")
    technician = db.relationship("Technician", backref="transfers")

class TransferItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    transfer_id = db.Column(db.Integer, db.ForeignKey("transfer.id"), nullable=False)
    warehouse_item_id = db.Column(db.Integer, db.ForeignKey("warehouse_item.id"))
    category = db.Column(db.String(50), default="")
    code = db.Column(db.String(80), default="")
    description = db.Column(db.String(255), default="")
    serial = db.Column(db.String(120), default="")
    quantity = db.Column(db.Integer, default=1)
    unit = db.Column(db.String(20), default="pz")
    transfer = db.relationship("Transfer", backref="items")
    warehouse_item = db.relationship("WarehouseItem")

# --- UTILITY ---

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def settings_obj():
    return AppSetting.query.first()

def now_it():
    return datetime.now().strftime("%d/%m/%Y %H:%M")

def next_bolla_no():
    prefix = settings_obj().bolla_prefix if settings_obj() else "BOL"
    return f"{prefix}-{Transfer.query.count() + 1:05d}"

def allowed_file(filename, extensions):
    return Path(filename).suffix.lower() in extensions

def excel_response(wb, filename):
    stream = BytesIO()
    wb.save(stream)
    stream.seek(0)
    return send_file(
        stream,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

# --- ROTTE ---

def register_routes(app):
    @app.route("/")
    def home():
        return redirect(url_for("dashboard" if current_user.is_authenticated else "login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            user = User.query.filter(
                func.lower(User.username) == request.form.get("username", "").strip().lower()
            ).first()
            if user and user.check_password(request.form.get("password", "")):
                login_user(user)
                return redirect(request.args.get("next") or url_for("dashboard"))
            flash("Credenziali non valide.", "danger")
        return render_template("login.html", title="Accesso")

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("login"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        stats = {
            "technicians": Technician.query.count(),
            "central_items": WarehouseItem.query.filter(WarehouseItem.assigned_to.is_(None)).count(),
            "mobile_items": WarehouseItem.query.filter(WarehouseItem.assigned_to.is_not(None)).count(),
            "open_charges": Charge.query.filter_by(status="aperto").count(),
            "transfers": Transfer.query.count(),
        }
        low_stock = WarehouseItem.query.filter(
            WarehouseItem.assigned_to.is_(None),
            WarehouseItem.quantity <= WarehouseItem.min_stock,
        ).order_by(WarehouseItem.id.desc()).limit(10).all()
        return render_template("dashboard.html", stats=stats, low_stock=low_stock, title="Dashboard")

    # --- GESTIONE TECNICI & ATTESTATI ---
    @app.route("/technician/<int:tech_id>")
    @login_required
    def technician_detail(tech_id):
        tech = Technician.query.get_or_404(tech_id)
        mobile_items = WarehouseItem.query.filter_by(assigned_to=tech.id).all()
        certs = Certificate.query.filter_by(technician_id=tech.id).all()
        return render_template("technician_detail.html", tech=tech, mobile_items=mobile_items, certs=certs, title=f"Scheda {tech.name}")

    @app.route("/technician/<int:tech_id>/upload_cert", methods=["POST"])
    @login_required
    def upload_cert(tech_id):
        tech = Technician.query.get_or_404(tech_id)
        file = request.files.get("cert_file")
        desc = request.form.get("description", "Certificato/Corso")
        if file and file.filename:
            if allowed_file(file.filename, ALLOWED_CERT_EXTENSIONS):
                ext = Path(secure_filename(file.filename)).suffix.lower()
                filename = f"tech_{tech.id}_{uuid4().hex}{ext}"
                file.save(os.path.join(app.config["CERT_FOLDER"], filename))
                
                new_cert = Certificate(technician_id=tech.id, filename=filename, description=desc)
                db.session.add(new_cert)
                db.session.commit()
                flash("Attestato salvato correttamente.", "success")
            else:
                flash("Formato file non supportato.", "danger")
        return redirect(url_for("technician_detail", tech_id=tech_id))

    @app.route("/certificate/view/<int:cert_id>")
    @login_required
    def view_cert(cert_id):
        cert = Certificate.query.get_or_404(cert_id)
        return send_from_directory(app.config["CERT_FOLDER"], cert.filename)

    # --- MAGAZZINO & BARCODE ---
    @app.route("/warehouse", methods=["GET", "POST"])
    @login_required
    def warehouse():
        # Logica Barcode: Se ricevo un codice via GET (da una scansione rapida)
        scan_code = request.args.get("scan")
        if scan_code:
            item = WarehouseItem.query.filter_by(code=scan_code, assigned_to=None).first()
            if item:
                item.quantity += 1
                db.session.commit()
                flash(f"Incrementata quantità per: {item.description}", "success")
                return redirect(url_for("warehouse"))

        if request.method == "POST":
            # Carico manuale
            db.session.add(WarehouseItem(
                code=request.form.get("code", "").strip(),
                category=request.form.get("category", "materiale"),
                description=request.form.get("description", "").strip(),
                quantity=int(request.form.get("quantity", "1")),
                unit=request.form.get("unit", "pz"),
                min_stock=int(request.form.get("min_stock", "0")),
            ))
            db.session.commit()
            flash("Articolo aggiunto.", "success")
            return redirect(url_for("warehouse"))

        items = WarehouseItem.query.filter(WarehouseItem.assigned_to.is_(None)).all()
        return render_template("warehouse.html", items=items, title="Magazzino")

    # --- IMPORT / EXCEL ---
    @app.route("/import/general", methods=["POST"])
    @login_required
    def import_general():
        file = request.files.get("file")
        if not file: return redirect(url_for("warehouse"))
        wb = load_workbook(file)
        ws = wb.active
        imported = 0
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row[0]: continue
            db.session.add(WarehouseItem(
                category=str(row[0]), code=str(row[1]), description=str(row[2]),
                quantity=int(row[5] or 1), unit=str(row[6] or "pz")
            ))
            imported += 1
        db.session.commit()
        flash(f"Importati {imported} articoli.", "success")
        return redirect(url_for("warehouse"))

    # [Nota: Qui puoi reinserire tutte le altre rotte (Transfers, Tools, Vans, Settings) dal tuo file originale]

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
