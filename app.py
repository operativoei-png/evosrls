import os
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from flask import Flask, flash, redirect, render_template, request, send_from_directory, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash
from openpyxl import load_workbook

# --- INIZIALIZZAZIONE ---
db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "login"

def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "evolve-industrial-2026")
    
    # Cartelle necessarie
    os.makedirs(app.instance_path, exist_ok=True)
    app.config["UPLOAD_FOLDER"] = os.path.join(app.root_path, "static", "uploads")
    app.config["CERT_FOLDER"] = os.path.join(app.config["UPLOAD_FOLDER"], "attestati")
    os.makedirs(app.config["CERT_FOLDER"], exist_ok=True)
    
    # Database: SQLite locale (ottimizzato per Render senza Postgres)
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        db_url = "sqlite:///" + os.path.join(app.instance_path, "evolve.db")
    elif db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
        
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)
    login_manager.init_app(app)

    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username="admin").first():
            u = User(username="admin", role="admin")
            u.set_password("admin123!")
            db.session.add(u)
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
    company_name = db.Column(db.String(150), default="Evolve Impianti")
    logo_path = db.Column(db.String(255), default="")
    bolla_prefix = db.Column(db.String(20), default="BOL")

class Technician(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(50), default="")
    notes = db.Column(db.String(255), default="")
    certificates = db.relationship("Certificate", backref="technician", cascade="all, delete-orphan")
    items = db.relationship("WarehouseItem", backref="technician")
    tools = db.relationship("Tool", backref="technician")
    vans = db.relationship("Van", backref="technician")
    charges = db.relationship("Charge", backref="technician")

class Certificate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    technician_id = db.Column(db.Integer, db.ForeignKey("technician.id"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    description = db.Column(db.String(150))
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)

class WarehouseItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(80), nullable=False)
    category = db.Column(db.String(50), default="Materiale")
    description = db.Column(db.String(255), nullable=False)
    serial = db.Column(db.String(120), unique=True, index=True)
    serialized = db.Column(db.Boolean, default=False)
    quantity = db.Column(db.Integer, default=1)
    unit = db.Column(db.String(20), default="pz")
    min_stock = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default="generale") 
    assigned_to = db.Column(db.Integer, db.ForeignKey("technician.id"))
    client_default = db.Column(db.String(150))
    last_update = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Tool(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(80), nullable=False)
    serial = db.Column(db.String(120))
    description = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(40), default="disponibile")
    charge_value = db.Column(db.Float, default=0.0)
    assigned_to = db.Column(db.Integer, db.ForeignKey("technician.id"))

class Van(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plate = db.Column(db.String(30), unique=True, nullable=False)
    model = db.Column(db.String(120))
    status = db.Column(db.String(40), default="attivo")
    assigned_to = db.Column(db.Integer, db.ForeignKey("technician.id"))

class Charge(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    description = db.Column(db.String(255), nullable=False)
    amount = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(50), default="aperto")
    notes = db.Column(db.String(255))
    technician_id = db.Column(db.Integer, db.ForeignKey("technician.id"))

class Transfer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bolla_no = db.Column(db.String(40), unique=True)
    transfer_type = db.Column(db.String(10)) 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    client = db.Column(db.String(150))
    job = db.Column(db.String(150))
    notes = db.Column(db.String(255))
    technician_id = db.Column(db.Integer, db.ForeignKey("technician.id"))
    technician = db.relationship("Technician")
    items = db.relationship("TransferItem", backref="transfer")

class TransferItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    transfer_id = db.Column(db.Integer, db.ForeignKey("transfer.id"))
    category = db.Column(db.String(50))
    code = db.Column(db.String(80))
    description = db.Column(db.String(255))
    serial = db.Column(db.String(120))
    quantity = db.Column(db.Integer)
    unit = db.Column(db.String(20))

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
            flash("Credenziali non valide", "error")
        return render_template("login.html")

    @app.route("/logout")
    def logout(): logout_user(); return redirect(url_for("login"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        stats = {
            "technicians": Technician.query.count(),
            "items_general": WarehouseItem.query.filter_by(status="generale").count(),
            "items_traveling": WarehouseItem.query.filter_by(status="in_viaggio").count(),
            "items_installed": WarehouseItem.query.filter_by(status="installato").count(),
            "open_charges": Charge.query.filter_by(status="aperto").count()
        }
        recent_assignments = WarehouseItem.query.filter_by(status="in_viaggio").order_by(WarehouseItem.last_update.desc()).limit(5).all()
        recent_charges = Charge.query.filter_by(status="aperto").order_by(Charge.at.desc()).limit(5).all() if hasattr(Charge, 'at') else []
        return render_template("dashboard.html", stats=stats, recent_assignments=recent_assignments, recent_charges=recent_charges)

    @app.route("/warehouse", methods=["GET", "POST"])
    @login_required
    def warehouse():
        if request.method == "POST":
            tech_id = request.form.get("technician_id")
            s = AppSetting.query.first()
            bolla_no = f"{s.bolla_prefix}-{datetime.now().year}-{Transfer.query.count() + 1:04d}"
            new_transfer = Transfer(bolla_no=bolla_no, transfer_type="out", technician_id=tech_id,
                                    client=request.form.get("client"), job=request.form.get("job"), notes=request.form.get("notes"))
            db.session.add(new_transfer)
            db.session.flush()
            count = 0
            raw_serials = request.form.get("serials", "")
            if raw_serials.strip():
                serial_list = [s.strip() for s in re.split(r'[\n,;]+', raw_serials) if s.strip()]
                for sn in serial_list:
                    item = WarehouseItem.query.filter_by(serial=sn).first()
                    if not item:
                        item = WarehouseItem(serial=sn, code="NEW", description="Articolo da Barcode", status="in_viaggio", assigned_to=tech_id)
                        db.session.add(item)
                    else:
                        item.status = "in_viaggio"
                        item.assigned_to = tech_id
                    db.session.add(TransferItem(transfer_id=new_transfer.id, code=item.code, description=item.description, serial=item.serial, quantity=1))
                    count += 1
            item_ids = request.form.getlist("item_ids")
            for i_id in item_ids:
                item = WarehouseItem.query.get(i_id)
                item.status = "in_viaggio"
                item.assigned_to = tech_id
                db.session.add(TransferItem(transfer_id=new_transfer.id, code=item.code, description=item.description, serial=item.serial, quantity=1))
                count += 1
            if count > 0:
                db.session.commit()
                flash(f"Bolla {bolla_no} creata.", "success")
            else:
                db.session.rollback()
            return redirect(url_for("warehouse"))
        items = WarehouseItem.query.filter_by(status="generale").all()
        technicians = Technician.query.all()
        transfers = Transfer.query.order_by(Transfer.created_at.desc()).limit(10).all()
        return render_template("warehouse.html", items=items, technicians=technicians, transfers=transfers)

    @app.route("/magazzino_generale", methods=["GET", "POST"])
    @login_required
    def magazzino_generale():
        if request.method == "POST":
            new_item = WarehouseItem(
                code=request.form.get("code"), category=request.form.get("category"),
                description=request.form.get("description"), unit=request.form.get("unit"),
                serialized=(request.form.get("serialized") == "si"),
                serial=request.form.get("serial") if request.form.get("serialized") == "si" else None,
                quantity=int(request.form.get("quantity") or 1),
                min_stock=int(request.form.get("min_stock") or 0),
                client_default=request.form.get("client_default"),
                status="generale"
            )
            db.session.add(new_item)
            db.session.commit()
            flash("Articolo salvato.", "success")
            return redirect(url_for("magazzino_generale"))
        items = WarehouseItem.query.filter_by(status="generale").all()
        return render_template("magazzino_generale.html", items=items)

    @app.route("/technicians", methods=["GET", "POST"])
    @login_required
    def technicians():
        if request.method == "POST":
            db.session.add(Technician(name=request.form.get("name"), phone=request.form.get("phone"), notes=request.form.get("notes")))
            db.session.commit()
        return render_template("technicians.html", technicians=Technician.query.all())

    @app.route("/technician/<int:tech_id>")
    @login_required
    def technician_detail(tech_id):
        tech = Technician.query.get_or_404(tech_id)
        mobile_items = WarehouseItem.query.filter_by(assigned_to=tech_id, status="in_viaggio").all()
        certs = Certificate.query.filter_by(technician_id=tech_id).all()
        return render_template("technician_detail.html", tech=tech, mobile_items=mobile_items, certs=certs)

    @app.route("/install_item/<int:item_id>", methods=["POST"])
    @login_required
    def install_item(item_id):
        item = WarehouseItem.query.get_or_404(item_id)
        item.status = "installato"
        db.session.commit()
        return redirect(request.referrer or url_for('dashboard'))

    @app.route("/upload_cert/<int:tech_id>", methods=["POST"])
    @login_required
    def upload_cert(tech_id):
        file = request.files.get("cert_file")
        if file:
            filename = f"tech_{tech_id}_{uuid4().hex}{Path(file.filename).suffix}"
            file.save(os.path.join(app.config["CERT_FOLDER"], filename))
            db.session.add(Certificate(technician_id=tech_id, filename=filename, description=request.form.get("description")))
            db.session.commit()
        return redirect(url_for("technician_detail", tech_id=tech_id))

    @app.route("/view_cert/<int:cert_id>")
    @login_required
    def view_cert(cert_id):
        cert = Certificate.query.get_or_404(cert_id)
        return send_from_directory(app.config["CERT_FOLDER"], cert.filename)

    @app.route("/charges", methods=["GET", "POST"])
    @login_required
    def charges():
        if request.method == "POST":
            db.session.add(Charge(technician_id=request.form.get("technician_id"), description=request.form.get("description"),
                                  amount=float(request.form.get("amount") or 0), notes=request.form.get("notes"), status="aperto"))
            db.session.commit()
        items = Charge.query.order_by(Charge.created_at.desc()).all()
        return render_template("charges.html", items=items, technicians=Technician.query.all())

    @app.route("/returns", methods=["GET", "POST"])
    @login_required
    def returns():
        if request.method == "POST":
            for m_id in request.form.getlist("material_ids"):
                item = WarehouseItem.query.get(m_id)
                item.status, item.assigned_to = "generale", None
            db.session.commit()
            return redirect(url_for("returns"))
        t_id = request.args.get("technician_id")
        data = {"technicians": Technician.query.all(), "selected_tech_id": t_id}
        if t_id:
            data.update({"tech_materials": WarehouseItem.query.filter_by(assigned_to=t_id, status="in_viaggio").all()})
        return render_template("returns.html", **data)

    @app.route("/tools", methods=["GET", "POST"])
    @login_required
    def tools():
        if request.method == "POST":
            db.session.add(Tool(code=request.form.get("code"), serial=request.form.get("serial"), description=request.form.get("description"),
                                charge_value=float(request.form.get("charge_value") or 0), assigned_to=request.form.get("assigned_to") or None))
            db.session.commit()
        return render_template("tools.html", items=Tool.query.all(), technicians=Technician.query.all())

    @app.route("/vans", methods=["GET", "POST"])
    @login_required
    def vans():
        if request.method == "POST":
            db.session.add(Van(plate=request.form.get("plate").upper(), model=request.form.get("model"), assigned_to=request.form.get("assigned_to") or None))
            db.session.commit()
        return render_template("vans.html", items=Van.query.all(), technicians=Technician.query.all())

    @app.route("/settings", methods=["GET", "POST"])
    @login_required
    def settings():
        s = AppSetting.query.first()
        if request.method == "POST":
            s.company_name = request.form.get("company_name")
            s.bolla_prefix = request.form.get("bolla_prefix")
            db.session.commit()
        return render_template("settings.html", settings_obj=s)

# --- AVVIO ---
app = create_app()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
