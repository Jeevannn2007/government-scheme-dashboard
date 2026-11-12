# %%
"""
Fuel Logistics: Station Networks, Underground Tanks & Sales Dispensation Dashboard
Single-file application: SQLite back end + a GUI that adapts to where it runs.

  * VS Code / terminal / IDLE  -> Tkinter desktop window
  * Jupyter / Google Colab     -> ipywidgets panel (no display driver needed)

Tkinter is imported lazily, so the script never crashes in cloud notebooks
where no graphical display exists.
"""

import html
import math
import sqlite3
import sys
from datetime import date, timedelta

DB_FILE = "fuel_logistics.db"
DEFAULT_CITY = "Not specified"
DEFAULT_CAPACITY = 30000.0
FUEL_TYPES = ("Petrol", "Diesel", "Premium")


# ==========================================================
# DATABASE LAYER
# ==========================================================

SCHEMA = """
CREATE TABLE IF NOT EXISTS stations (
    station_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    station_name  TEXT NOT NULL UNIQUE COLLATE NOCASE,
    location_city TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tanks (
    tank_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    station_id      INTEGER NOT NULL
                    REFERENCES stations(station_id) ON DELETE CASCADE,
    fuel_type       TEXT NOT NULL,
    capacity_litres REAL NOT NULL CHECK (capacity_litres > 0)
);
CREATE TABLE IF NOT EXISTS sales_logs (
    log_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tank_id           INTEGER NOT NULL
                      REFERENCES tanks(tank_id) ON DELETE CASCADE,
    dispensation_date TEXT NOT NULL,
    volume_litres     REAL NOT NULL CHECK (volume_litres > 0)
);
"""

# (tab title, column headers, SQL)
VIEWS = [
    (
        "Master Consumption Audit",
        ("Station Name", "Location City", "Fuel Type", "Dispensation Date", "Volume (L)"),
        """SELECT s.station_name, s.location_city, t.fuel_type,
                  l.dispensation_date, l.volume_litres
           FROM sales_logs l
           JOIN tanks t    ON t.tank_id    = l.tank_id
           JOIN stations s ON s.station_id = t.station_id
           ORDER BY l.dispensation_date DESC, l.log_id DESC""",
    ),
    (
        "Stations",
        ("station_id", "station_name", "location_city"),
        "SELECT station_id, station_name, location_city FROM stations ORDER BY station_id",
    ),
    (
        "Tanks",
        ("tank_id", "station_id", "fuel_type", "capacity_litres"),
        "SELECT tank_id, station_id, fuel_type, capacity_litres FROM tanks ORDER BY tank_id",
    ),
    (
        "Sales Logs",
        ("log_id", "tank_id", "dispensation_date", "volume_litres"),
        "SELECT log_id, tank_id, dispensation_date, volume_litres FROM sales_logs ORDER BY log_id",
    ),
]

DELETE_STATION = "Delete an Entire Station Profile"
DELETE_TANK = "Terminate a Specific Storage Tank Entry"
DELETE_KINDS = (DELETE_STATION, DELETE_TANK)


def validate_entry(station_name, fuel_type, volume_text):
    """Return cleaned (station, fuel, volume) or raise ValueError with a clean message."""
    station_name = " ".join(str(station_name).split())
    fuel_type = " ".join(str(fuel_type).split())
    volume_text = str(volume_text).strip()

    if not station_name or not fuel_type or not volume_text:
        raise ValueError("Station Name, Fuel Type and Dispensed Volume are all required.")
    try:
        volume = float(volume_text.replace(",", ""))
    except ValueError:
        raise ValueError("Dispensed Volume must be a number (for example 1250.5).") from None
    if not math.isfinite(volume):
        raise ValueError("Dispensed Volume must be a real number.")
    if volume < 0:
        raise ValueError("Dispensed Volume cannot be negative.")
    if volume == 0:
        raise ValueError("Dispensed Volume must be greater than zero.")
    if volume > DEFAULT_CAPACITY:
        raise ValueError(
            f"Dispensed Volume cannot exceed the tank capacity ({DEFAULT_CAPACITY:,.0f} L)."
        )
    return station_name, fuel_type.title(), round(volume, 2)


class FuelDatabase:
    def __init__(self, path=DB_FILE):
        # check_same_thread=False: notebook widget callbacks may run on another thread
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.execute("PRAGMA foreign_keys = ON")  # must be set on every connection
        if self.con.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise RuntimeError("SQLite foreign key enforcement could not be enabled.")
        self.con.executescript(SCHEMA)
        self._seed_if_empty()

    # ---------- setup ----------
    def _seed_if_empty(self):
        if self.con.execute("SELECT COUNT(*) FROM stations").fetchone()[0]:
            return
        today = date.today()
        stations = [
            ("Northgate Fuel Hub", "Austin"),
            ("Riverside Petrol Bank", "Denver"),
            ("Harbor Point Station", "Seattle"),
        ]
        tanks = [  # (station_id, fuel, capacity)
            (1, "Petrol", 40000), (1, "Diesel", 35000),
            (2, "Petrol", 30000), (2, "Premium", 20000),
            (3, "Diesel", 45000), (3, "Premium", 25000),
        ]
        volumes = [
            (1250.5, 980.0, 1430.75, 760.0), (2100.0, 1875.25, 1540.0, 1320.5),
            (890.0, 1020.5, 765.25, 940.0), (410.0, 385.5, 520.0, 460.75),
            (2650.0, 2480.5, 2790.0, 2310.25), (615.0, 700.5, 585.0, 655.25),
        ]
        with self.con:
            self.con.executemany("INSERT INTO stations (station_name, location_city) VALUES (?, ?)", stations)
            self.con.executemany("INSERT INTO tanks (station_id, fuel_type, capacity_litres) VALUES (?, ?, ?)", tanks)
            for tank_id, vols in enumerate(volumes, start=1):
                for days_ago, vol in enumerate(vols, start=1):
                    self.con.execute(
                        "INSERT INTO sales_logs (tank_id, dispensation_date, volume_litres) VALUES (?, ?, ?)",
                        (tank_id, (today - timedelta(days=days_ago * 2)).isoformat(), vol),
                    )

    # ---------- reads ----------
    def rows(self, sql):
        """Run a SELECT and format floats for display."""
        return [
            tuple(f"{v:,.2f}" if isinstance(v, float) else v for v in row)
            for row in self.con.execute(sql).fetchall()
        ]

    def delete_targets(self, kind):
        """Return [(label, id)] for the delete dropdown of the chosen classification."""
        if kind == DELETE_STATION:
            q = "SELECT station_id, station_name || ' (' || location_city || ')' FROM stations ORDER BY station_name"
        else:
            q = """SELECT t.tank_id,
                          'Tank #' || t.tank_id || ' - ' || s.station_name || ' - ' || t.fuel_type
                   FROM tanks t JOIN stations s ON s.station_id = t.station_id
                   ORDER BY s.station_name, t.tank_id"""
        return [(label, _id) for _id, label in self.con.execute(q)]

    # ---------- writes ----------
    def add_sale(self, station_name, fuel_type, volume_text):
        """Validate, link/create the station, then add a tank and a sales log. Returns a message."""
        station_name, fuel_type, volume = validate_entry(station_name, fuel_type, volume_text)
        with self.con:  # one transaction: all rows are saved or none are
            row = self.con.execute(
                "SELECT station_id FROM stations WHERE station_name = ? COLLATE NOCASE", (station_name,)
            ).fetchone()
            if row:
                station_id, new_station = row[0], False
            else:
                station_id = self.con.execute(
                    "INSERT INTO stations (station_name, location_city) VALUES (?, ?)",
                    (station_name, DEFAULT_CITY),
                ).lastrowid
                new_station = True
            tank_id = self.con.execute(
                "INSERT INTO tanks (station_id, fuel_type, capacity_litres) VALUES (?, ?, ?)",
                (station_id, fuel_type, DEFAULT_CAPACITY),
            ).lastrowid
            self.con.execute(
                "INSERT INTO sales_logs (tank_id, dispensation_date, volume_litres) VALUES (?, ?, ?)",
                (tank_id, date.today().isoformat(), volume),
            )
        origin = "New station created" if new_station else "Linked to existing station"
        return (f"{origin} (ID {station_id}). Added tank #{tank_id} ({fuel_type}) "
                f"and a sales log of {volume:,.2f} L.")

    def delete(self, kind, target_id):
        """Delete a station or tank (children are removed by ON DELETE CASCADE)."""
        if target_id is None:
            raise ValueError("Please choose a record to delete.")
        with self.con:
            if kind == DELETE_STATION:
                tanks = self.con.execute("SELECT COUNT(*) FROM tanks WHERE station_id = ?", (target_id,)).fetchone()[0]
                logs = self.con.execute(
                    "SELECT COUNT(*) FROM sales_logs WHERE tank_id IN (SELECT tank_id FROM tanks WHERE station_id = ?)",
                    (target_id,),
                ).fetchone()[0]
                self.con.execute("DELETE FROM stations WHERE station_id = ?", (target_id,))
                return f"Station deleted along with {tanks} tank(s) and {logs} sales log(s)."
            logs = self.con.execute("SELECT COUNT(*) FROM sales_logs WHERE tank_id = ?", (target_id,)).fetchone()[0]
            self.con.execute("DELETE FROM tanks WHERE tank_id = ?", (target_id,))
            return f"Tank terminated along with {logs} sales log(s)."


# ==========================================================
# DESKTOP GUI (Tkinter, imported lazily)
# ==========================================================

def launch_desktop_ui(db):
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
        root = tk.Tk()
    except Exception as exc:  # no tkinter installed, or no display available
        print("Tkinter window could not be opened:", exc)
        print("Run this file in a Jupyter/Colab cell to get the notebook interface instead.")
        return

    root.title("Fuel Logistics Operations Dashboard")
    root.geometry("1100x720")

    station_var, fuel_var, volume_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
    kind_var, target_var = tk.StringVar(value=DELETE_KINDS[0]), tk.StringVar()
    target_ids = {}

    # ---- panels ----
    top = ttk.Frame(root, padding=10)
    top.pack(fill="x")
    top.columnconfigure((0, 1), weight=1, uniform="panel")

    ins = ttk.LabelFrame(top, text="Operations Insert Panel", padding=10)
    ins.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
    ttk.Label(ins, text="Station Name").grid(row=0, column=0, sticky="w", pady=4)
    ttk.Entry(ins, textvariable=station_var, width=32).grid(row=0, column=1, pady=4, padx=6)
    ttk.Label(ins, text="Fuel Type").grid(row=1, column=0, sticky="w", pady=4)
    ttk.Combobox(ins, textvariable=fuel_var, values=FUEL_TYPES, width=29).grid(row=1, column=1, pady=4, padx=6)
    ttk.Label(ins, text="Dispensed Volume (L)").grid(row=2, column=0, sticky="w", pady=4)
    ttk.Entry(ins, textvariable=volume_var, width=32).grid(row=2, column=1, pady=4, padx=6)

    dele = ttk.LabelFrame(top, text="Operations Delete Panel", padding=10)
    dele.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
    ttk.Label(dele, text="Classification").grid(row=0, column=0, sticky="w", pady=4)
    kind_box = ttk.Combobox(dele, textvariable=kind_var, values=DELETE_KINDS, state="readonly", width=40)
    kind_box.grid(row=0, column=1, pady=4, padx=6)
    ttk.Label(dele, text="Target Record").grid(row=1, column=0, sticky="w", pady=4)
    target_box = ttk.Combobox(dele, textvariable=target_var, state="readonly", width=40)
    target_box.grid(row=1, column=1, pady=4, padx=6)

    # ---- tabbed views ----
    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))
    trees = []
    for title, headers, _sql in VIEWS:
        frame = ttk.Frame(notebook)
        notebook.add(frame, text=title)
        tree = ttk.Treeview(frame, columns=headers, show="headings")
        for h in headers:
            tree.heading(h, text=h)
            tree.column(h, width=130, anchor="center", stretch=True)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        trees.append(tree)

    # ---- behaviour ----
    def refresh_targets(*_):
        items = db.delete_targets(kind_var.get())
        target_ids.clear()
        target_ids.update(dict(items))
        target_box["values"] = [label for label, _ in items]
        target_var.set("")

    def refresh_all():
        for tree, (_t, _h, sql) in zip(trees, VIEWS):
            tree.delete(*tree.get_children())
            for row in db.rows(sql):
                tree.insert("", "end", values=row)
        refresh_targets()

    def on_add():
        try:
            msg = db.add_sale(station_var.get(), fuel_var.get(), volume_var.get())
        except ValueError as exc:
            messagebox.showerror("Invalid Input", str(exc))
            return
        station_var.set("")
        fuel_var.set("")
        volume_var.set("")
        refresh_all()
        messagebox.showinfo("Record Added", msg)

    def on_delete():
        try:
            msg = db.delete(kind_var.get(), target_ids.get(target_var.get()))
        except ValueError as exc:
            messagebox.showerror("Nothing Selected", str(exc))
            return
        refresh_all()
        messagebox.showinfo("Record Deleted", msg)

    ttk.Button(ins, text="Add Record", command=on_add).grid(row=3, column=1, sticky="e", pady=8, padx=6)
    ttk.Button(dele, text="Delete Selected", command=on_delete).grid(row=2, column=1, sticky="e", pady=8, padx=6)
    kind_box.bind("<<ComboboxSelected>>", refresh_targets)

    refresh_all()
    root.mainloop()


# ==========================================================
# NOTEBOOK GUI (ipywidgets: Jupyter / Colab)
# ==========================================================

def _html_table(headers, rows):
    th = "".join(
        f"<th style='border:1px solid #8886;padding:4px 10px;text-align:left'>{html.escape(str(h))}</th>"
        for h in headers
    )
    body = "".join(
        "<tr>" + "".join(
            f"<td style='border:1px solid #8884;padding:4px 10px'>{html.escape(str(c))}</td>" for c in r
        ) + "</tr>"
        for r in rows
    ) or f"<tr><td colspan='{len(headers)}' style='padding:6px'>No records.</td></tr>"
    return (
        "<div style='max-height:340px;overflow:auto'>"
        f"<table style='border-collapse:collapse;font-size:13px'><tr>{th}</tr>{body}</table></div>"
    )


def launch_notebook_ui(db):
    try:
        import ipywidgets as w
        from IPython.display import display
    except ImportError:
        print("ipywidgets is required in notebooks. Run:  %pip install ipywidgets  and re-run this cell.")
        return

    station = w.Text(description="Station", placeholder="Station name", layout=w.Layout(width="360px"))
    fuel = w.Combobox(description="Fuel Type", placeholder="Petrol / Diesel / Premium",
                      options=list(FUEL_TYPES), ensure_option=False, layout=w.Layout(width="360px"))
    volume = w.Text(description="Volume (L)", placeholder="e.g. 1250.5", layout=w.Layout(width="360px"))
    add_btn = w.Button(description="Add Record", button_style="success")

    kind = w.Dropdown(description="Classification", options=DELETE_KINDS, layout=w.Layout(width="520px"),
                      style={"description_width": "110px"})
    target = w.Dropdown(description="Target", options=[], layout=w.Layout(width="520px"),
                        style={"description_width": "110px"})
    del_btn = w.Button(description="Delete Selected", button_style="danger")

    message = w.HTML()
    tab = w.Tab(children=[w.HTML() for _ in VIEWS])
    titles = [v[0] for v in VIEWS]
    try:
        tab.titles = tuple(titles)
    except Exception:
        for i, t in enumerate(titles):
            tab.set_title(i, t)

    def say(text, ok=True):
        color = "#1a7f37" if ok else "#d1242f"
        message.value = f"<b style='color:{color}'>{html.escape(text)}</b>"

    def refresh_targets(*_):
        target.options = db.delete_targets(kind.value)  # [(label, id)]
        if not target.options:
            target.value = None

    def refresh_all():
        for panel, (_t, headers, sql) in zip(tab.children, VIEWS):
            panel.value = _html_table(headers, db.rows(sql))
        refresh_targets()

    def on_add(_):
        try:
            msg = db.add_sale(station.value, fuel.value, volume.value)
        except ValueError as exc:
            say(f"Error: {exc}", ok=False)
            return
        station.value = fuel.value = volume.value = ""
        refresh_all()
        say(msg)

    def on_delete(_):
        try:
            msg = db.delete(kind.value, target.value)
        except ValueError as exc:
            say(f"Error: {exc}", ok=False)
            return
        refresh_all()
        say(msg)

    add_btn.on_click(on_add)
    del_btn.on_click(on_delete)
    kind.observe(refresh_targets, names="value")

    refresh_all()
    display(w.VBox([
        w.HTML("<h3>Fuel Logistics Operations Dashboard</h3>"),
        w.HTML("<b>Operations Insert Panel</b>"), station, fuel, volume, add_btn,
        w.HTML("<b>Operations Delete Panel</b>"), kind, target, del_btn,
        message, tab,
    ]))


# ==========================================================
# ENTRY POINT
# ==========================================================

def running_in_notebook():
    if "google.colab" in sys.modules:
        return True
    try:
        from IPython import get_ipython
        shell = get_ipython()
        return shell is not None and shell.__class__.__name__ == "ZMQInteractiveShell"
    except ImportError:
        return False


def main():
    db = FuelDatabase()
    launch_desktop_ui(db)


if __name__ == "__main__":
    main()

# %%



