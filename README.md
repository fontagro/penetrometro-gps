# 📍 Penetrómetro GPS Mapper

App web que lee CSVs de penetrómetro desde **Dropbox**, filtra datos inválidos, y muestra las mediciones en un **mapa interactivo** que cualquiera puede ver con el link.

## Funcionalidades

- **Lectura automática de Dropbox**: Lee todos los `.csv` de una carpeta configurada
- **Filtrado inteligente**: Descarta mediciones sin GPS válido o sin fuerza registrada
- **Mapa interactivo**: Puntos GPS con tamaño y color proporcional a la fuerza máxima
- **Popup con datos completos**: Fuerza máxima, mínima, promedio, distancia, cantidad de lecturas
- **Gráfico de perfil**: Al hacer click en un punto, muestra la curva completa de penetración
- **Filtros por fecha/archivo/rango de fuerza**: Seleccioná qué jornadas visualizar
- **3 capas de mapa**: Oscuro, Satélite, Calles (con switch en la esquina)
- **Exportar CSV**: Descargá los datos filtrados
- **Subida manual**: También podés arrastrar CSVs directamente

## Instalación rápida

```bash
# 1. Instalar dependencias
pip install -r requirements.txt

# 2. Configurar Dropbox (ver abajo)
# Editar .streamlit/secrets.toml con tu token

# 3. Ejecutar
streamlit run app.py
```

## Configurar Dropbox

1. Ir a [Dropbox Developers](https://www.dropbox.com/developers/apps)
2. **Create app** → Scoped access → Full Dropbox → Nombre: "Penetrometro Mapper"
3. Pestaña **Permissions**: habilitar `files.metadata.read` y `files.content.read` → Submit
4. Pestaña **Settings**: click **Generate** en "Generated access token"
5. Copiar el token en `.streamlit/secrets.toml`

```toml
DROPBOX_ACCESS_TOKEN = "sl.xxxxxxxxxxxxx..."
DROPBOX_FOLDER = "/Penetrometro"
```

6. Subir los CSVs del penetrómetro a la carpeta `/Penetrometro` en tu Dropbox

## Deploy público (Streamlit Cloud)

Para que cualquiera con el link pueda verlo:

1. Subir el proyecto a un repo de GitHub (privado o público)
2. Ir a [share.streamlit.io](https://share.streamlit.io)
3. Conectar el repo → seleccionar `app.py`
4. En **Advanced settings** → **Secrets**, pegar el contenido de `secrets.toml`
5. Deploy → Listo, compartí el link

## Estructura de los CSV

La app espera el formato estándar del penetrómetro digital:

```
Fecha;Hora;Latitud;Longitud;Medicion;Fuerza [KG];Distancia [CM];Distancia total;Fuerza Promedio;Fuerza Maxima;Distancia Fuerza Maxima;
21/09/2020;21:00:08;-26824099;-60841991;0152; ; ; ; ; ; ;
;; ; ; ;   6;0000; ; ; ; ;
;; ; ; ;   9;0001; ; ; ; ;
...
;; ; ; ; ; ;51;26.54;49.00;51;
```

- Las coordenadas GPS vienen como enteros (se dividen por 1.000.000)
- Cada bloque empieza con una línea que tiene Fecha y termina con la línea de resumen
- Se soportan bloques con múltiples sub-rangos de profundidad
