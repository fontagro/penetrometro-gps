"""
Penetrómetro GPS Mapper
=======================
App Streamlit que lee CSVs de penetrómetro desde GitHub,
muestra mediciones en un mapa con lotes delineados,
y detalle de profundidad vs compactación al hacer click.
"""

import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import plotly.graph_objects as go
import json
from datetime import datetime
from pathlib import Path

st.set_page_config(page_title="Penetrómetro GPS", page_icon="📍", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&family=JetBrains+Mono:wght@400;600&display=swap');
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    .main .block-container { padding-top: 1.5rem; padding-bottom: 1rem; }
    .app-title {
        font-family: 'DM Sans', sans-serif; font-size: 28px; font-weight: 700;
        background: linear-gradient(135deg, #60a5fa, #a78bfa);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        margin-bottom: 0; line-height: 1.2;
    }
    .app-subtitle { font-family: 'DM Sans', sans-serif; font-size: 14px; color: #6b7280; margin-top: 2px; }
    div[data-testid="stMetricValue"] { font-family: 'JetBrains Mono', monospace; }
    section[data-testid="stSidebar"] { background: #16181f; }
</style>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════
# PARSER
# ═══════════════════════════════════════════════════════════════════

def parse_penetrometro_csv(content: str, filename: str) -> list[dict]:
    lines = content.replace('\r\n', '\n').replace('\r', '\n').strip().split('\n')
    if len(lines) < 2:
        return []
    measurements = []
    current = None
    for line in lines[1:]:
        parts = [p.strip() for p in line.split(';')]
        if len(parts) < 11:
            continue
        fecha, hora = parts[0], parts[1]
        lat_raw, lon_raw = parts[2], parts[3]
        medicion = parts[4]
        fuerza_kg, distancia_cm = parts[5], parts[6]
        dist_total, fuerza_prom = parts[7], parts[8]
        fuerza_max, dist_fmax = parts[9], parts[10]
        if fecha:
            if current is not None:
                _finalize(current)
                if current.get('valid'):
                    measurements.append(current)
            current = {
                'fecha': fecha, 'hora': hora, 'lat_raw': lat_raw, 'lon_raw': lon_raw,
                'medicion': medicion, 'archivo': filename, 'readings': [],
                'fuerza_max': None, 'fuerza_min': None, 'fuerza_prom': None,
                'dist_total': None, 'dist_fmax': None, 'valid': False,
                'prof_max_compactacion': None,
            }
            continue
        if current is None:
            continue
        if fuerza_kg:
            try:
                f = int(fuerza_kg)
                if f > 0:
                    d = int(distancia_cm) if distancia_cm else 0
                    current['readings'].append((f, d))
            except (ValueError, TypeError):
                pass
        if dist_total or fuerza_max:
            try:
                fm = float(fuerza_max.replace(',', '.')) if fuerza_max else 0
                fp = float(fuerza_prom.replace(',', '.')) if fuerza_prom else 0
                dt = int(dist_total) if dist_total else 0
                dfm = int(dist_fmax) if dist_fmax else 0
                if fm <= 0:
                    continue
                if current['fuerza_max'] is None or fm > current['fuerza_max']:
                    current['fuerza_max'] = fm
                    current['dist_fmax'] = dfm
                if fp > 0:
                    current['fuerza_prom'] = fp if current['fuerza_prom'] is None else (current['fuerza_prom'] + fp) / 2
                if current['dist_total'] is None or dt > current['dist_total']:
                    current['dist_total'] = dt
            except (ValueError, TypeError):
                pass
    if current is not None:
        _finalize(current)
        if current.get('valid'):
            measurements.append(current)
    return measurements


def _finalize(m):
    try:
        lat = int(m['lat_raw']) / 1_000_000
        lon = int(m['lon_raw']) / 1_000_000
    except (ValueError, TypeError):
        m['valid'] = False; return
    if abs(lat) < 1 or abs(lon) < 1:
        m['valid'] = False; return
    m['lat'], m['lon'] = lat, lon
    if not m['readings']:
        m['valid'] = False; return
    fuerzas = [r[0] for r in m['readings']]
    profundidades = [r[1] for r in m['readings']]
    m['fuerza_min'] = min(fuerzas)
    m['cant_lecturas'] = len(fuerzas)
    max_idx = fuerzas.index(max(fuerzas))
    m['prof_max_compactacion'] = profundidades[max_idx]
    if m['fuerza_max'] is None or m['fuerza_max'] <= 0:
        m['valid'] = False; return
    if m['fuerza_prom'] is None:
        m['fuerza_prom'] = round(sum(fuerzas) / len(fuerzas), 2)
    try:
        m['datetime'] = datetime.strptime(f"{m['fecha']} {m['hora']}", "%d/%m/%Y %H:%M:%S")
    except ValueError:
        m['datetime'] = None
    m['valid'] = True


# ═══════════════════════════════════════════════════════════════════
# DATOS — Lee CSVs desde la carpeta data/ del repo
# ═══════════════════════════════════════════════════════════════════

@st.cache_data(ttl=1800, show_spinner="Cargando datos...")
def load_data():
    """Lee todos los CSV de la carpeta data/ del repo."""
    data_dir = Path(__file__).parent / "data"
    if not data_dir.exists():
        return []

    all_m = []
    seen = set()
    for csv_file in sorted(data_dir.glob("*.CSV")):
        if csv_file.name.lower() in seen:
            continue
        seen.add(csv_file.name.lower())
        try:
            content = csv_file.read_text(encoding='latin-1')
            all_m.extend(parse_penetrometro_csv(content, csv_file.name))
        except Exception:
            pass

    # También buscar .csv en minúscula
    for csv_file in sorted(data_dir.glob("*.csv")):
        if csv_file.name.lower() in seen:
            continue
        seen.add(csv_file.name.lower())
        try:
            content = csv_file.read_text(encoding='latin-1')
            all_m.extend(parse_penetrometro_csv(content, csv_file.name))
        except Exception:
            pass

    return all_m


@st.cache_data(show_spinner=False)
def load_lotes():
    p = Path(__file__).parent / "lotes.json"
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return []


def get_color(f):
    if f < 20: return '#22c55e'
    if f < 40: return '#eab308'
    if f < 55: return '#f97316'
    return '#ef4444'


# ═══════════════════════════════════════════════════════════════════
# POPUP
# ═══════════════════════════════════════════════════════════════════

def build_popup(m):
    c = get_color(m['fuerza_max'])
    pct = min(100, m['fuerza_max'] / 65 * 100)
    prof = m.get('prof_max_compactacion', '—')
    prof_s = f"{prof} CM" if isinstance(prof, (int, float)) else "—"

    rows = ""
    if m.get('readings'):
        readings = m['readings']
        step = max(1, len(readings) // 8)
        sample = readings[::step][:8]
        max_r = max(readings, key=lambda r: r[0])
        for f, d in sample:
            is_max = f == max_r[0] and d == max_r[1]
            bg = f"background:rgba({','.join(str(int(c.lstrip('#')[i:i+2],16)) for i in (0,2,4))},0.2);" if is_max else ""
            fw = "font-weight:700;" if is_max else ""
            mk = " ⬅" if is_max else ""
            rows += f'<tr style="{bg}"><td style="padding:2px 6px;{fw}">{d}</td><td style="padding:2px 6px;text-align:right;{fw}">{f}{mk}</td></tr>'

    tbl = f"""<div style="margin-top:8px;padding-top:6px;border-top:1px solid #e5e7eb;">
        <div style="font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:3px;">Prof. vs Compactación</div>
        <table style="width:100%;font-size:11px;border-collapse:collapse;font-family:'JetBrains Mono',monospace;">
        <tr style="color:#6b7280;"><td style="padding:2px 6px;">CM</td><td style="padding:2px 6px;text-align:right;">KG</td></tr>
        {rows}</table></div>""" if rows else ""

    return f"""<div style="font-family:'DM Sans',sans-serif;min-width:210px;padding:4px;">
        <div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.8px;">Medición</div>
        <div style="font-family:'JetBrains Mono',monospace;font-size:15px;font-weight:600;color:#a78bfa;margin-bottom:6px;">#{m['medicion']}</div>
        <div style="font-size:11px;color:#6b7280;text-transform:uppercase;">Fuerza Máxima</div>
        <div style="font-size:26px;font-weight:700;color:{c};font-family:'JetBrains Mono',monospace;line-height:1.1;">
            {m['fuerza_max']:.0f} <span style="font-size:12px;color:#6b7280;">KG</span></div>
        <div style="height:3px;background:#e5e7eb;border-radius:2px;margin:4px 0 8px;">
            <div style="height:100%;width:{pct:.0f}%;background:{c};border-radius:2px;"></div></div>
        <table style="width:100%;font-size:12px;border-collapse:collapse;">
        <tr><td style="color:#6b7280;padding:2px 0;">Prof. máx compactación</td><td style="text-align:right;font-weight:700;color:{c};">{prof_s}</td></tr>
        <tr><td style="color:#6b7280;padding:2px 0;">Fza. Promedio</td><td style="text-align:right;font-weight:600;">{m['fuerza_prom']:.1f} KG</td></tr>
        <tr><td style="color:#6b7280;padding:2px 0;">Fza. Mínima</td><td style="text-align:right;">{m['fuerza_min']} KG</td></tr>
        <tr style="border-top:1px solid #e5e7eb;"><td style="color:#6b7280;padding:4px 0 0;">Fecha</td><td style="text-align:right;">{m['fecha']} {m['hora']}</td></tr>
        </table>{tbl}</div>"""


# ═══════════════════════════════════════════════════════════════════
# MAPA
# ═══════════════════════════════════════════════════════════════════

LOT_COLORS = ['#3b82f6','#8b5cf6','#06b6d4','#10b981','#f59e0b','#ec4899','#6366f1','#14b8a6','#f97316','#84cc16','#a855f7','#0ea5e9','#22c55e','#eab308','#ef4444']

def create_map(measurements, lotes):
    if not measurements:
        return folium.Map(location=[-27.0, -60.9], zoom_start=10)
    lats = [m['lat'] for m in measurements]
    lons = [m['lon'] for m in measurements]
    center = [sum(lats)/len(lats), sum(lons)/len(lons)]
    fmap = folium.Map(location=center, zoom_start=13, tiles=None)

    folium.TileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', name='Oscuro', attr='CARTO').add_to(fmap)
    folium.TileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', name='Satélite', attr='Esri').add_to(fmap)
    folium.TileLayer('openstreetmap', name='Calles').add_to(fmap)

    if lotes:
        lg = folium.FeatureGroup(name='🗺️ Lotes', show=True)
        for i, lote in enumerate(lotes):
            col = LOT_COLORS[i % len(LOT_COLORS)]
            name = lote.get('name', lote.get('filename', f'Lote {i+1}'))
            folium.Polygon(locations=lote['coords'], color=col, weight=2, opacity=0.7,
                          fill=True, fill_color=col, fill_opacity=0.08, tooltip=name).add_to(lg)
            cl = [sum(c[0] for c in lote['coords'])/len(lote['coords']),
                  sum(c[1] for c in lote['coords'])/len(lote['coords'])]
            folium.Marker(location=cl, icon=folium.DivIcon(
                html=f'<div style="font-size:10px;color:{col};font-weight:600;white-space:nowrap;text-shadow:0 0 3px rgba(0,0,0,0.8);">{name}</div>',
                icon_size=(100,20), icon_anchor=(50,10))).add_to(lg)
        lg.add_to(fmap)

    mg = folium.FeatureGroup(name='📊 Mediciones', show=True)
    mx = max(m['fuerza_max'] for m in measurements)
    for m in measurements:
        c = get_color(m['fuerza_max'])
        r = 7 + (m['fuerza_max']/mx)*13
        prof = m.get('prof_max_compactacion', '?')
        folium.CircleMarker(location=[m['lat'],m['lon']], radius=r, color='white', weight=1.5,
                           opacity=0.4, fill=True, fill_color=c, fill_opacity=0.8,
                           popup=folium.Popup(build_popup(m), max_width=320),
                           tooltip=f"#{m['medicion']} — {m['fuerza_max']:.0f} KG @ {prof} CM").add_to(mg)
    mg.add_to(fmap)

    legend = """<div style="position:fixed;bottom:30px;left:10px;z-index:1000;background:rgba(22,24,31,0.92);padding:12px 16px;border-radius:8px;border:1px solid rgba(255,255,255,0.1);font-family:'DM Sans',sans-serif;font-size:12px;color:#e4e4e7;">
        <div style="font-weight:700;margin-bottom:6px;">Fuerza Máxima (KG)</div>
        <div><span style="color:#22c55e;">●</span> &lt; 20 — Baja</div>
        <div><span style="color:#eab308;">●</span> 20–40 — Media</div>
        <div><span style="color:#f97316;">●</span> 40–55 — Alta</div>
        <div><span style="color:#ef4444;">●</span> &gt; 55 — Crítica</div></div>"""
    fmap.get_root().html.add_child(folium.Element(legend))
    folium.LayerControl(collapsed=False).add_to(fmap)
    fmap.fit_bounds([[min(lats)-0.005,min(lons)-0.005],[max(lats)+0.005,max(lons)+0.005]])
    return fmap


# ═══════════════════════════════════════════════════════════════════
# GRÁFICO PROFUNDIDAD VS COMPACTACIÓN
# ═══════════════════════════════════════════════════════════════════

def build_chart(m):
    if not m.get('readings'):
        return None
    fuerzas = [r[0] for r in m['readings']]
    profs = [r[1] for r in m['readings']]
    c = get_color(m['fuerza_max'])
    mi = fuerzas.index(max(fuerzas))

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=fuerzas, y=profs, mode='lines+markers',
        line=dict(color=c, width=2.5), marker=dict(size=6, color=c),
        fill='tozerox', fillcolor=f"rgba({','.join(str(int(c.lstrip('#')[i:i+2],16)) for i in (0,2,4))},0.1)",
        name='Compactación', hovertemplate='Fuerza: %{x} KG<br>Profundidad: %{y} CM<extra></extra>'))
    fig.add_trace(go.Scatter(x=[fuerzas[mi]], y=[profs[mi]], mode='markers+text',
        marker=dict(size=14, color='#ef4444', symbol='diamond', line=dict(color='white',width=2)),
        text=[f'{fuerzas[mi]} KG @ {profs[mi]} CM'], textposition='top right',
        textfont=dict(color='#ef4444',size=12), name='Máx', showlegend=False,
        hovertemplate='⚠️ MÁXIMA<br>Fuerza: %{x} KG<br>Prof: %{y} CM<extra></extra>'))
    if m.get('fuerza_prom'):
        fig.add_vline(x=m['fuerza_prom'], line_dash="dash", line_color="#6b7280",
            annotation_text=f"Prom: {m['fuerza_prom']:.1f}", annotation_font_color="#9ca3af")

    fig.update_layout(title=f"Perfil — Medición #{m['medicion']}", xaxis_title="Fuerza (KG)",
        yaxis_title="Profundidad (CM)", yaxis=dict(autorange='reversed'),
        template="plotly_dark", paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        height=400, margin=dict(l=50,r=20,t=50,b=50), font=dict(family="DM Sans"), showlegend=False)
    fig.update_xaxes(gridcolor='rgba(255,255,255,0.05)')
    fig.update_yaxes(gridcolor='rgba(255,255,255,0.05)')
    return fig


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main():
    st.markdown('<div class="app-title">📍 Penetrómetro GPS Mapper</div>', unsafe_allow_html=True)
    st.markdown('<div class="app-subtitle">Mediciones de resistencia del suelo · Profundidad vs Compactación</div>', unsafe_allow_html=True)
    st.markdown("")

    measurements = load_data()
    lotes = load_lotes()

    with st.sidebar:
        if st.button("🔄 Actualizar datos", use_container_width=True):
            load_data.clear()
            st.rerun()

    if not measurements:
        st.info("No se encontraron datos.\n\nSubí archivos CSV a la carpeta `data/` del repositorio en GitHub.")
        return

    with st.sidebar:
        st.markdown("---")
        st.markdown("### 🔍 Filtros")
        fechas = sorted(set(m['fecha'] for m in measurements))
        sel_fechas = st.multiselect("Fechas", fechas, default=fechas)
        archivos = sorted(set(m['archivo'] for m in measurements))
        sel_archivos = st.multiselect("Archivos", archivos, default=archivos)
        forces_all = [m['fuerza_max'] for m in measurements]
        f_range = st.slider("Rango Fuerza Máx (KG)", int(min(forces_all)), int(max(forces_all)),
                           (int(min(forces_all)), int(max(forces_all))))

    filtered = [m for m in measurements if m['fecha'] in sel_fechas
                and m['archivo'] in sel_archivos and f_range[0] <= m['fuerza_max'] <= f_range[1]]
    if not filtered:
        st.warning("No hay mediciones con los filtros seleccionados.")
        return

    fs = [m['fuerza_max'] for m in filtered]
    c1,c2,c3,c4,c5 = st.columns(5)
    c1.metric("Mediciones", len(filtered))
    c2.metric("Fza. Máxima", f"{max(fs):.0f} KG")
    c3.metric("Fza. Mínima", f"{min(fs):.0f} KG")
    c4.metric("Promedio", f"{sum(fs)/len(fs):.1f} KG")
    c5.metric("Jornadas", len(set(m['fecha'] for m in filtered)))

    st.markdown("---")
    fmap = create_map(filtered, lotes)
    map_data = st_folium(fmap, width=None, height=550, returned_objects=["last_object_clicked"])

    clicked = map_data.get("last_object_clicked")
    if clicked:
        clat, clng = clicked.get("lat"), clicked.get("lng")
        if clat and clng:
            best = min(filtered, key=lambda m: (m['lat']-clat)**2 + (m['lon']-clng)**2)
            st.markdown("---")
            st.markdown(f"### 📊 Perfil — Medición #{best['medicion']}")
            col1, col2 = st.columns([1, 2])
            with col1:
                prof = best.get('prof_max_compactacion', '—')
                st.markdown(f"""
| Dato | Valor |
|------|-------|
| **Medición** | #{best['medicion']} |
| **Fecha** | {best['fecha']} {best['hora']} |
| **Fza. Máxima** | {best['fuerza_max']:.0f} KG |
| **Prof. Máx. Compact.** | {prof} CM |
| **Fza. Promedio** | {best['fuerza_prom']:.1f} KG |
| **Fza. Mínima** | {best['fuerza_min']} KG |
| **Lecturas** | {best.get('cant_lecturas','—')} |
| **GPS** | {best['lat']:.6f}, {best['lon']:.6f} |
""")
            with col2:
                fig = build_chart(best)
                if fig:
                    st.plotly_chart(fig, use_container_width=True)
            if best.get('readings'):
                with st.expander("📋 Lecturas completas"):
                    st.dataframe(pd.DataFrame(best['readings'], columns=['Fuerza (KG)','Profundidad (CM)']),
                               use_container_width=True, hide_index=True)

    with st.expander("📋 Tabla general", expanded=False):
        st.dataframe(pd.DataFrame([{
            'Med.': m['medicion'], 'Fecha': m['fecha'],
            'Fza.Máx': m['fuerza_max'], 'Prof.Máx.Comp.(CM)': m.get('prof_max_compactacion','—'),
            'Fza.Prom': m['fuerza_prom'], 'Fza.Mín': m['fuerza_min'],
            'Lect.': m.get('cant_lecturas'), 'Archivo': m['archivo'],
        } for m in filtered]), use_container_width=True, hide_index=True)

    with st.sidebar:
        st.markdown("---")
        st.markdown("### 📥 Exportar")
        df_e = pd.DataFrame([{
            'medicion': m['medicion'], 'fecha': m['fecha'], 'hora': m['hora'],
            'lat': m['lat'], 'lon': m['lon'], 'fuerza_max': m['fuerza_max'],
            'prof_max_compact_cm': m.get('prof_max_compactacion'),
            'fuerza_min': m['fuerza_min'], 'fuerza_prom': m['fuerza_prom'],
            'lecturas': m.get('cant_lecturas'), 'archivo': m['archivo'],
        } for m in filtered])
        st.download_button("⬇️ Descargar CSV", data=df_e.to_csv(index=False, sep=';').encode('utf-8'),
                          file_name="penetrometro.csv", mime="text/csv", use_container_width=True)


if __name__ == "__main__":
    main()
