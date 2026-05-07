"""
Penetrómetro GPS Mapper
=======================
App Streamlit que lee CSVs de penetrómetro desde Dropbox,
parsea mediciones con GPS, y muestra un mapa interactivo público.

Requisitos:
  pip install streamlit dropbox folium streamlit-folium pandas plotly

Ejecución:
  streamlit run app.py

Configurar en .streamlit/secrets.toml:
  DROPBOX_ACCESS_TOKEN = "tu_token_aqui"
  DROPBOX_FOLDER = "/Penetrometro"   # carpeta en Dropbox con los CSVs
"""

import streamlit as st
import dropbox
import pandas as pd
import folium
from streamlit_folium import st_folium
import plotly.graph_objects as go
import io
import os
import re
from datetime import datetime
from pathlib import Path

# ─── Configuración de página ───────────────────────────────────────
st.set_page_config(
    page_title="Penetrómetro GPS",
    page_icon="📍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── CSS personalizado ─────────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&family=JetBrains+Mono:wght@400;600&display=swap');

    /* Ocultar menú hamburguesa y footer */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}

    .main .block-container {
        padding-top: 1.5rem;
        padding-bottom: 1rem;
    }

    /* Título principal */
    .app-title {
        font-family: 'DM Sans', sans-serif;
        font-size: 28px;
        font-weight: 700;
        background: linear-gradient(135deg, #60a5fa, #a78bfa);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0;
        line-height: 1.2;
    }

    .app-subtitle {
        font-family: 'DM Sans', sans-serif;
        font-size: 14px;
        color: #6b7280;
        margin-top: 2px;
    }

    /* Cards de métricas */
    .metric-card {
        background: #1e2030;
        border: 1px solid rgba(255,255,255,0.06);
        border-radius: 12px;
        padding: 16px 20px;
        text-align: center;
    }

    .metric-value {
        font-family: 'JetBrains Mono', monospace;
        font-size: 32px;
        font-weight: 700;
        line-height: 1.1;
    }

    .metric-label {
        font-family: 'DM Sans', sans-serif;
        font-size: 12px;
        color: #6b7280;
        text-transform: uppercase;
        letter-spacing: 0.8px;
        margin-top: 4px;
    }

    /* Colores de fuerza */
    .force-low { color: #22c55e; }
    .force-med { color: #eab308; }
    .force-high { color: #f97316; }
    .force-crit { color: #ef4444; }

    /* Sidebar styling */
    section[data-testid="stSidebar"] {
        background: #16181f;
    }

    /* Tabla de datos */
    .stDataFrame {
        font-family: 'JetBrains Mono', monospace;
        font-size: 13px;
    }

    div[data-testid="stMetricValue"] {
        font-family: 'JetBrains Mono', monospace;
    }
</style>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════
# PARSER DE CSV DEL PENETRÓMETRO
# ═══════════════════════════════════════════════════════════════════

def parse_penetrometro_csv(content: str, filename: str) -> list[dict]:
    """
    Parsea un CSV de penetrómetro.

    Reglas de validación (las 3 deben cumplirse):
      1. GPS válido: Latitud y Longitud distintos de 0
      2. Lecturas con datos: columna F (Fuerza KG) debe tener valores > 0
         Si la columna F está vacía o es 0 en todas las filas, se descarta
      3. Resumen válido: debe existir al menos una línea de resumen con
         Fuerza Maxima > 0 (última fila del bloque)

    Las mediciones que no existen en el archivo (ej: 153, 155) simplemente
    no aparecen — no se inventan.
    """
    lines = content.replace('\r\n', '\n').replace('\r', '\n').strip().split('\n')
    if len(lines) < 2:
        return []

    measurements = []
    current = None

    for line in lines[1:]:  # Skip header
        parts = [p.strip() for p in line.split(';')]
        if len(parts) < 11:
            continue

        fecha = parts[0]
        hora = parts[1]
        lat_raw = parts[2]
        lon_raw = parts[3]
        medicion = parts[4]
        fuerza_kg = parts[5]
        distancia_cm = parts[6]
        dist_total = parts[7]
        fuerza_prom = parts[8]
        fuerza_max = parts[9]
        dist_fmax = parts[10]

        # ── Nuevo bloque de medición ──
        if fecha:
            if current is not None:
                _finalize_measurement(current)
                if current.get('valid'):
                    measurements.append(current)

            current = {
                'fecha': fecha,
                'hora': hora,
                'lat_raw': lat_raw,
                'lon_raw': lon_raw,
                'medicion': medicion,
                'archivo': filename,
                'readings': [],          # lista de (fuerza, distancia) con fuerza > 0
                'fuerza_max': None,
                'fuerza_min': None,
                'fuerza_prom': None,
                'dist_total': None,
                'dist_fmax': None,
                'valid': False,
            }
            continue

        if current is None:
            continue

        # ── Lectura individual de fuerza (columna F) ──
        # Solo tomar si tiene dato y es > 0
        if fuerza_kg:
            try:
                f = int(fuerza_kg)
                if f > 0:
                    d = int(distancia_cm) if distancia_cm else 0
                    current['readings'].append((f, d))
                # Si f == 0, se ignora la lectura
            except (ValueError, TypeError):
                pass

        # ── Línea de resumen (Fuerza Maxima del bloque) ──
        if (dist_total or fuerza_max):
            try:
                fm = float(fuerza_max.replace(',', '.')) if fuerza_max else 0
                fp = float(fuerza_prom.replace(',', '.')) if fuerza_prom else 0
                dt = int(dist_total) if dist_total else 0
                dfm = int(dist_fmax) if dist_fmax else 0

                # Ignorar líneas de resumen con fuerza_max = 0
                if fm <= 0:
                    continue

                # Tomar el mayor si hay múltiples sub-rangos
                if current['fuerza_max'] is None or fm > current['fuerza_max']:
                    current['fuerza_max'] = fm
                    current['dist_fmax'] = dfm

                if fp > 0:
                    if current['fuerza_prom'] is None:
                        current['fuerza_prom'] = fp
                    else:
                        # Promediar los sub-rangos (solo los válidos)
                        current['fuerza_prom'] = (current['fuerza_prom'] + fp) / 2

                if current['dist_total'] is None or dt > current['dist_total']:
                    current['dist_total'] = dt
            except (ValueError, TypeError):
                pass

    # Último bloque
    if current is not None:
        _finalize_measurement(current)
        if current.get('valid'):
            measurements.append(current)

    return measurements


def _finalize_measurement(m: dict):
    """
    Completa y valida una medición.

    Una medición es válida SOLO si cumple las 3 condiciones:
      1. GPS válido (lat/lon != 0)
      2. Tiene lecturas con fuerza > 0 en columna F
      3. Tiene al menos un resumen con fuerza_max > 0
    """
    # ── Condición 1: GPS válido ──
    try:
        lat = int(m['lat_raw']) / 1_000_000
        lon = int(m['lon_raw']) / 1_000_000
    except (ValueError, TypeError):
        m['valid'] = False
        return

    if abs(lat) < 1 or abs(lon) < 1:
        m['valid'] = False
        return

    m['lat'] = lat
    m['lon'] = lon

    # ── Condición 2: Tiene lecturas con fuerza > 0 ──
    if not m['readings']:
        m['valid'] = False
        return

    fuerzas = [r[0] for r in m['readings']]
    m['fuerza_min_lectura'] = min(fuerzas)
    m['fuerza_max_lectura'] = max(fuerzas)
    m['cant_lecturas'] = len(fuerzas)
    m['fuerza_min'] = min(fuerzas)

    # ── Condición 3: Tiene resumen válido con fuerza_max > 0 ──
    if m['fuerza_max'] is None or m['fuerza_max'] <= 0:
        m['valid'] = False
        return

    # Si no hay promedio del resumen, calcularlo de las lecturas
    if m['fuerza_prom'] is None:
        m['fuerza_prom'] = round(sum(fuerzas) / len(fuerzas), 2)

    # Parsear fecha
    try:
        m['datetime'] = datetime.strptime(f"{m['fecha']} {m['hora']}", "%d/%m/%Y %H:%M:%S")
    except ValueError:
        try:
            m['datetime'] = datetime.strptime(m['fecha'], "%d/%m/%Y")
        except ValueError:
            m['datetime'] = None

    m['valid'] = True


# ═══════════════════════════════════════════════════════════════════
# FUENTES DE DATOS
# ═══════════════════════════════════════════════════════════════════

def load_from_dropbox() -> list[dict]:
    """Carga CSVs desde Dropbox, buscando recursivamente.
    Si un archivo con el mismo nombre aparece en varias carpetas,
    solo toma el primero (evita duplicados)."""
    token = st.secrets.get("DROPBOX_ACCESS_TOKEN", "")
    folder = st.secrets.get("DROPBOX_FOLDER", "")

    # Normalizar ruta: Dropbox API usa "" para raíz, no "/"
    folder = folder.strip()
    if folder == "/" or folder == "":
        folder = ""
    else:
        # Asegurar que empiece con / y no termine con /
        if not folder.startswith("/"):
            folder = "/" + folder
        folder = folder.rstrip("/")

    if not token:
        return []

    try:
        dbx = dropbox.Dropbox(token)
        all_measurements = []

        # Buscar CSVs recursivamente
        csv_files = []
        seen_names = set()
        result = dbx.files_list_folder(folder, recursive=True)

        while True:
            for entry in result.entries:
                if (isinstance(entry, dropbox.files.FileMetadata)
                        and entry.name.lower().endswith('.csv')):
                    # Deduplicar por nombre de archivo
                    if entry.name.lower() not in seen_names:
                        csv_files.append(entry)
                        seen_names.add(entry.name.lower())
            if not result.has_more:
                break
            result = dbx.files_list_folder_continue(result.cursor)

        if not csv_files:
            st.warning("No se encontraron archivos CSV en Dropbox.")
            return []

        progress = st.progress(0, text=f"Leyendo {len(csv_files)} archivos de Dropbox...")
        for i, entry in enumerate(csv_files):
            progress.progress(
                (i + 1) / len(csv_files),
                text=f"Procesando {entry.name}..."
            )
            _, response = dbx.files_download(entry.path_lower)
            content = response.content.decode('latin-1')
            measurements = parse_penetrometro_csv(content, entry.name)
            all_measurements.extend(measurements)

        progress.empty()
        return all_measurements

    except dropbox.exceptions.AuthError:
        st.error("❌ Token de Dropbox inválido. Revisá `.streamlit/secrets.toml`.")
        return []
    except dropbox.exceptions.ApiError as e:
        st.error(f"❌ Error de Dropbox: {e}")
        return []


def load_from_upload(uploaded_files) -> list[dict]:
    """Carga CSVs subidos manualmente."""
    all_measurements = []
    for uf in uploaded_files:
        content = uf.read().decode('latin-1')
        measurements = parse_penetrometro_csv(content, uf.name)
        all_measurements.extend(measurements)
    return all_measurements


def load_local_demo() -> list[dict]:
    """Carga CSVs de la carpeta local de uploads (para demo)."""
    upload_dir = Path("/mnt/user-data/uploads")
    all_measurements = []
    if upload_dir.exists():
        for csv_file in sorted(upload_dir.glob("*.CSV")):
            content = csv_file.read_text(encoding='latin-1')
            measurements = parse_penetrometro_csv(content, csv_file.name)
            all_measurements.extend(measurements)
    return all_measurements


# ═══════════════════════════════════════════════════════════════════
# MAPA
# ═══════════════════════════════════════════════════════════════════

def get_force_color(fuerza: float) -> str:
    if fuerza < 20:
        return '#22c55e'   # verde
    elif fuerza < 40:
        return '#eab308'   # amarillo
    elif fuerza < 55:
        return '#f97316'   # naranja
    return '#ef4444'       # rojo


def get_force_class(fuerza: float) -> str:
    if fuerza < 20:
        return 'force-low'
    elif fuerza < 40:
        return 'force-med'
    elif fuerza < 55:
        return 'force-high'
    return 'force-crit'


def build_popup_html(m: dict) -> str:
    color = get_force_color(m['fuerza_max'])
    pct = min(100, (m['fuerza_max'] / 65) * 100)
    
    f_min = m.get('fuerza_min', m.get('fuerza_min_lectura', '—'))
    if isinstance(f_min, (int, float)):
        f_min = f"{f_min:.0f}"

    dist = m.get('dist_total', '—')
    if isinstance(dist, (int, float)):
        dist = f"{dist}"

    lecturas = m.get('cant_lecturas', '—')

    return f"""
    <div style="font-family:'DM Sans',sans-serif;min-width:240px;padding:4px;">
        <div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.8px;">
            Medición
        </div>
        <div style="font-family:'JetBrains Mono',monospace;font-size:15px;font-weight:600;color:#a78bfa;margin-bottom:8px;">
            #{m['medicion']}
        </div>

        <div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.8px;">
            Fuerza Máxima
        </div>
        <div style="font-size:30px;font-weight:700;color:{color};font-family:'JetBrains Mono',monospace;line-height:1.1;">
            {m['fuerza_max']:.0f} <span style="font-size:14px;color:#6b7280;">KG</span>
        </div>
        <div style="height:4px;background:#e5e7eb;border-radius:2px;margin:6px 0 10px;">
            <div style="height:100%;width:{pct:.0f}%;background:{color};border-radius:2px;"></div>
        </div>

        <table style="width:100%;font-size:12px;border-collapse:collapse;">
            <tr>
                <td style="color:#6b7280;padding:3px 0;">Fuerza Mínima</td>
                <td style="text-align:right;font-weight:600;">{f_min} KG</td>
            </tr>
            <tr>
                <td style="color:#6b7280;padding:3px 0;">Fuerza Promedio</td>
                <td style="text-align:right;font-weight:600;">{m['fuerza_prom']:.1f} KG</td>
            </tr>
            <tr>
                <td style="color:#6b7280;padding:3px 0;">Dist. Total</td>
                <td style="text-align:right;font-weight:600;">{dist} CM</td>
            </tr>
            <tr>
                <td style="color:#6b7280;padding:3px 0;">Lecturas</td>
                <td style="text-align:right;font-weight:600;">{lecturas}</td>
            </tr>
            <tr style="border-top:1px solid #e5e7eb;">
                <td style="color:#6b7280;padding:5px 0 2px;">Fecha</td>
                <td style="text-align:right;font-weight:600;">{m['fecha']}</td>
            </tr>
            <tr>
                <td style="color:#6b7280;padding:2px 0;">Hora</td>
                <td style="text-align:right;font-weight:600;">{m['hora']}</td>
            </tr>
            <tr>
                <td style="color:#6b7280;padding:2px 0;">Archivo</td>
                <td style="text-align:right;font-weight:500;font-size:11px;">{m['archivo']}</td>
            </tr>
        </table>
    </div>
    """


def create_map(measurements: list[dict]) -> folium.Map:
    """Crea mapa Folium con los puntos de medición."""
    if not measurements:
        return folium.Map(location=[-27.0, -60.9], zoom_start=10)

    lats = [m['lat'] for m in measurements]
    lons = [m['lon'] for m in measurements]
    center = [sum(lats) / len(lats), sum(lons) / len(lons)]

    fmap = folium.Map(
        location=center,
        zoom_start=13,
        tiles=None,
    )

    # Capas de mapa
    folium.TileLayer(
        'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
        name='Oscuro',
        attr='CARTO',
    ).add_to(fmap)

    folium.TileLayer(
        'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        name='Satélite',
        attr='Esri',
    ).add_to(fmap)

    folium.TileLayer(
        'openstreetmap',
        name='Calles',
    ).add_to(fmap)

    # Agregar marcadores
    max_force_global = max(m['fuerza_max'] for m in measurements)

    for m in measurements:
        color = get_force_color(m['fuerza_max'])
        radius = 7 + (m['fuerza_max'] / max_force_global) * 13

        folium.CircleMarker(
            location=[m['lat'], m['lon']],
            radius=radius,
            color='white',
            weight=1.5,
            opacity=0.4,
            fill=True,
            fill_color=color,
            fill_opacity=0.8,
            popup=folium.Popup(
                build_popup_html(m),
                max_width=300,
            ),
            tooltip=f"#{m['medicion']} — {m['fuerza_max']:.0f} KG",
        ).add_to(fmap)

    # Leyenda
    legend_html = """
    <div style="position:fixed;bottom:30px;left:10px;z-index:1000;
                background:rgba(22,24,31,0.92);padding:12px 16px;
                border-radius:8px;border:1px solid rgba(255,255,255,0.1);
                font-family:'DM Sans',sans-serif;font-size:12px;color:#e4e4e7;">
        <div style="font-weight:700;margin-bottom:6px;">Fuerza Máxima (KG)</div>
        <div><span style="color:#22c55e;">●</span> &lt; 20 — Baja</div>
        <div><span style="color:#eab308;">●</span> 20–40 — Media</div>
        <div><span style="color:#f97316;">●</span> 40–55 — Alta</div>
        <div><span style="color:#ef4444;">●</span> &gt; 55 — Crítica</div>
    </div>
    """
    fmap.get_root().html.add_child(folium.Element(legend_html))

    folium.LayerControl().add_to(fmap)
    fmap.fit_bounds([[min(lats) - 0.005, min(lons) - 0.005],
                     [max(lats) + 0.005, max(lons) + 0.005]])

    return fmap


# ═══════════════════════════════════════════════════════════════════
# GRÁFICO DE PERFIL DE PENETRACIÓN
# ═══════════════════════════════════════════════════════════════════

def build_profile_chart(m: dict) -> go.Figure:
    """Gráfico de fuerza vs lectura (perfil de resistencia)."""
    if not m.get('readings'):
        return None

    fuerzas = [r[0] for r in m['readings']]
    x_vals = list(range(1, len(fuerzas) + 1))

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x_vals,
        y=fuerzas,
        mode='lines+markers',
        line=dict(color=get_force_color(m['fuerza_max']), width=2.5),
        marker=dict(size=5, color=get_force_color(m['fuerza_max'])),
        fill='tozeroy',
        fillcolor=f"rgba({','.join(str(int(get_force_color(m['fuerza_max']).lstrip('#')[i:i+2], 16)) for i in (0,2,4))},0.15)",
        name='Fuerza',
        hovertemplate='Lectura %{x}<br>Fuerza: %{y} KG<extra></extra>',
    ))

    # Línea de promedio
    if m.get('fuerza_prom'):
        fig.add_hline(
            y=m['fuerza_prom'],
            line_dash="dash",
            line_color="#6b7280",
            annotation_text=f"Prom: {m['fuerza_prom']:.1f} KG",
            annotation_position="top right",
            annotation_font_color="#9ca3af",
        )

    fig.update_layout(
        title=f"Perfil de Penetración — Medición #{m['medicion']}",
        xaxis_title="Nº Lectura",
        yaxis_title="Fuerza (KG)",
        template="plotly_dark",
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        height=350,
        margin=dict(l=50, r=20, t=50, b=40),
        font=dict(family="DM Sans"),
    )
    fig.update_xaxes(gridcolor='rgba(255,255,255,0.05)')
    fig.update_yaxes(gridcolor='rgba(255,255,255,0.05)')

    return fig


# ═══════════════════════════════════════════════════════════════════
# APP PRINCIPAL
# ═══════════════════════════════════════════════════════════════════

def main():
    # ── Header ──
    st.markdown('<div class="app-title">📍 Penetrómetro GPS Mapper</div>', unsafe_allow_html=True)
    st.markdown('<div class="app-subtitle">Visualización de mediciones de resistencia del suelo</div>', unsafe_allow_html=True)
    st.markdown("")

    # ── Sidebar: Fuente de datos ──
    with st.sidebar:
        st.markdown("### ⚙️ Configuración")

        source = st.radio(
            "Fuente de datos",
            ["📂 Dropbox", "📎 Subir archivos", "🗂️ Demo (archivos locales)"],
            index=0,
            help="Seleccioná de dónde cargar los CSVs del penetrómetro",
        )

        measurements = []

        if source == "📂 Dropbox":
            st.markdown("---")
            if st.button("🔄 Cargar desde Dropbox", use_container_width=True):
                measurements = load_from_dropbox()
                st.session_state['measurements'] = measurements

            measurements = st.session_state.get('measurements', [])

        elif source == "📎 Subir archivos":
            uploaded = st.file_uploader(
                "Arrastrá los CSV del penetrómetro",
                type=['csv'],
                accept_multiple_files=True,
            )
            if uploaded:
                measurements = load_from_upload(uploaded)
                st.session_state['measurements'] = measurements

        else:  # Demo
            if 'demo_loaded' not in st.session_state:
                measurements = load_local_demo()
                st.session_state['measurements'] = measurements
                st.session_state['demo_loaded'] = True
            measurements = st.session_state.get('measurements', [])

    # ── Sin datos ──
    if not measurements:
        st.info(
            "👈 Seleccioná una fuente de datos en el panel lateral para comenzar.\n\n"
            "La app lee CSVs generados por el penetrómetro, extrae los puntos GPS válidos "
            "y muestra cada medición en el mapa con su fuerza máxima, mínima, promedio "
            "y perfil de resistencia completo."
        )
        return

    # ── Sidebar: Filtros ──
    with st.sidebar:
        st.markdown("---")
        st.markdown("### 🔍 Filtros")

        # Fechas disponibles
        fechas = sorted(set(m['fecha'] for m in measurements))
        selected_fechas = st.multiselect(
            "Fechas",
            fechas,
            default=fechas,
            help="Seleccioná qué jornadas de campo mostrar",
        )

        # Archivos disponibles
        archivos = sorted(set(m['archivo'] for m in measurements))
        selected_archivos = st.multiselect(
            "Archivos",
            archivos,
            default=archivos,
        )

        # Rango de fuerza
        all_forces = [m['fuerza_max'] for m in measurements]
        f_min_global, f_max_global = min(all_forces), max(all_forces)

        force_range = st.slider(
            "Rango de Fuerza Máxima (KG)",
            min_value=int(f_min_global),
            max_value=int(f_max_global),
            value=(int(f_min_global), int(f_max_global)),
        )

    # ── Aplicar filtros ──
    filtered = [
        m for m in measurements
        if m['fecha'] in selected_fechas
        and m['archivo'] in selected_archivos
        and force_range[0] <= m['fuerza_max'] <= force_range[1]
    ]

    if not filtered:
        st.warning("No hay mediciones con los filtros seleccionados.")
        return

    # ── Métricas ──
    forces = [m['fuerza_max'] for m in filtered]
    avg_f = sum(forces) / len(forces)
    max_f = max(forces)
    min_f = min(forces)

    cols = st.columns(5)
    with cols[0]:
        st.metric("Mediciones", len(filtered))
    with cols[1]:
        st.metric("Fza. Máxima", f"{max_f:.0f} KG")
    with cols[2]:
        st.metric("Fza. Mínima", f"{min_f:.0f} KG")
    with cols[3]:
        st.metric("Promedio", f"{avg_f:.1f} KG")
    with cols[4]:
        fechas_count = len(set(m['fecha'] for m in filtered))
        st.metric("Jornadas", fechas_count)

    # ── Mapa ──
    st.markdown("---")
    fmap = create_map(filtered)
    map_data = st_folium(
        fmap,
        width=None,
        height=550,
        returned_objects=["last_object_clicked"],
    )

    # ── Detalle al hacer click ──
    clicked = map_data.get("last_object_clicked")
    if clicked:
        click_lat = clicked.get("lat")
        click_lng = clicked.get("lng")

        if click_lat and click_lng:
            # Encontrar la medición más cercana
            best = min(
                filtered,
                key=lambda m: (m['lat'] - click_lat)**2 + (m['lon'] - click_lng)**2
            )

            st.markdown("---")
            st.markdown(f"### 📊 Detalle — Medición #{best['medicion']}")

            detail_cols = st.columns([1, 1, 1])

            with detail_cols[0]:
                st.markdown(f"""
                | Campo | Valor |
                |-------|-------|
                | **Medición** | #{best['medicion']} |
                | **Fecha** | {best['fecha']} |
                | **Hora** | {best['hora']} |
                | **Archivo** | {best['archivo']} |
                | **Lat / Lon** | {best['lat']:.6f}, {best['lon']:.6f} |
                """)

            with detail_cols[1]:
                st.markdown(f"""
                | Métrica | Valor |
                |---------|-------|
                | **Fuerza Máxima** | {best['fuerza_max']:.0f} KG |
                | **Fuerza Mínima** | {best.get('fuerza_min', best.get('fuerza_min_lectura', '—'))} KG |
                | **Fuerza Promedio** | {best['fuerza_prom']:.1f} KG |
                | **Distancia Total** | {best.get('dist_total', '—')} CM |
                | **Lecturas** | {best.get('cant_lecturas', '—')} |
                """)

            with detail_cols[2]:
                # Distribución de fuerza
                if best.get('readings'):
                    fuerzas_readings = [r[0] for r in best['readings']]
                    low = sum(1 for f in fuerzas_readings if f < 20)
                    med = sum(1 for f in fuerzas_readings if 20 <= f < 40)
                    high = sum(1 for f in fuerzas_readings if 40 <= f < 55)
                    crit = sum(1 for f in fuerzas_readings if f >= 55)
                    st.markdown(f"""
                    | Rango | Lecturas |
                    |-------|----------|
                    | 🟢 < 20 KG | {low} |
                    | 🟡 20–40 KG | {med} |
                    | 🟠 40–55 KG | {high} |
                    | 🔴 > 55 KG | {crit} |
                    """)

            # Gráfico de perfil
            fig = build_profile_chart(best)
            if fig:
                st.plotly_chart(fig, use_container_width=True)

    # ── Tabla de datos ──
    with st.expander("📋 Tabla de datos completa", expanded=False):
        df = pd.DataFrame([
            {
                'Medición': m['medicion'],
                'Fecha': m['fecha'],
                'Hora': m['hora'],
                'Lat': round(m['lat'], 6),
                'Lon': round(m['lon'], 6),
                'Fza. Máx (KG)': m['fuerza_max'],
                'Fza. Mín (KG)': m.get('fuerza_min', m.get('fuerza_min_lectura', None)),
                'Fza. Prom (KG)': m['fuerza_prom'],
                'Dist. Total (CM)': m.get('dist_total'),
                'Lecturas': m.get('cant_lecturas'),
                'Archivo': m['archivo'],
            }
            for m in filtered
        ])
        st.dataframe(df, use_container_width=True, hide_index=True)

    # ── Exportar ──
    with st.sidebar:
        st.markdown("---")
        st.markdown("### 📥 Exportar")

        df_export = pd.DataFrame([
            {
                'medicion': m['medicion'],
                'fecha': m['fecha'],
                'hora': m['hora'],
                'latitud': m['lat'],
                'longitud': m['lon'],
                'fuerza_max_kg': m['fuerza_max'],
                'fuerza_min_kg': m.get('fuerza_min', m.get('fuerza_min_lectura')),
                'fuerza_prom_kg': m['fuerza_prom'],
                'dist_total_cm': m.get('dist_total'),
                'cant_lecturas': m.get('cant_lecturas'),
                'archivo': m['archivo'],
            }
            for m in filtered
        ])

        csv_data = df_export.to_csv(index=False, sep=';').encode('utf-8')
        st.download_button(
            "⬇️ Descargar CSV filtrado",
            data=csv_data,
            file_name="penetrometro_mediciones.csv",
            mime="text/csv",
            use_container_width=True,
        )

        # Sidebar info
        st.markdown("---")
        st.markdown(
            f"<div style='font-size:11px;color:#6b7280;'>"
            f"📊 {len(measurements)} mediciones totales<br>"
            f"📁 {len(archivos)} archivos cargados<br>"
            f"📅 {len(fechas)} jornadas de campo"
            f"</div>",
            unsafe_allow_html=True,
        )


if __name__ == "__main__":
    main()
