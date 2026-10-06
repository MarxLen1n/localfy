from dataclasses import dataclass
from typing import List, Tuple, Optional
from pathlib import Path
import sqlite3
import functools

from utils import SQL_ERROR

def try_sql(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except sqlite3.Error as e:
            raise SQL_ERROR(f"{e}")

    return wrapper

@dataclass
class Cancion:
    url: str
    ruta: Path
    nombre: str
    artista: Optional[str] = ""

@dataclass
class Playlist:
    id: int
    nombre: str
    canciones: List[Cancion]

class BaseDeDatos:
    def __init__(self, ruta_base: str):
        self.conexion = sqlite3.connect(ruta_base)

        # Necesario para que funcionen los ON DELETE CASCADE
        self.conexion.execute("PRAGMA foreign_keys = ON")

        self.cursor = self.conexion.cursor()

        self.crear_base_de_datos()

    @try_sql
    def crear_base_de_datos(self) -> None:
        self.cursor.executescript("""
            CREATE TABLE IF NOT EXISTS canciones (
                url TEXT PRIMARY KEY,
                nombre TEXT NOT NULL,
                ruta TEXT NOT NULL,
                artista TEXT
            );

            CREATE TABLE IF NOT EXISTS playlists (
                id INTEGER PRIMARY KEY,
                nombre TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS canciones_playlists (
                cancion_url TEXT NOT NULL,
                playlist_id INTEGER NOT NULL,

                PRIMARY KEY (cancion_url, playlist_id),

                FOREIGN KEY (cancion_url)
                    REFERENCES canciones(url)
                    ON DELETE CASCADE,

                FOREIGN KEY (playlist_id)
                    REFERENCES playlists(id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS playlists_youtube (
                playlist_id INTEGER NOT NULL,
                playlist_url TEXT NOT NULL,

                PRIMARY KEY (playlist_id, playlist_url),

                FOREIGN KEY (playlist_id)
                    REFERENCES playlists(id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS canciones_eliminadas (
                url TEXT PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS canciones_procesadas (
                url TEXT PRIMARY KEY
            );

            INSERT OR IGNORE INTO playlists (id, nombre) VALUES (0, 'Favoritas')
        """)

        self.conexion.commit()

    # ---------------------------------------------------------
    # CANCIONES
    # ---------------------------------------------------------
    
    # Busqueda

    @try_sql
    def buscar_canciones(self, busqueda: str) -> List[Cancion]:
        busqueda = f"%{busqueda}%"

        filas = self.cursor.execute("""
            SELECT 
                c.url, 
                c.ruta, 
                c.nombre, 
                c.artista
            FROM canciones AS c
            WHERE 
                c.nombre LIKE ? OR c.artista LIKE ?
                OR EXISTS (
                    SELECT 1 FROM canciones_playlists AS cp
                    JOIN playlists AS p ON p.id = cp.playlist_id
                    WHERE cp.cancion_url = c.url AND p.nombre LIKE ?
                )
        """, (busqueda, busqueda, busqueda)).fetchall()

        return [
            Cancion(
                url = url,
                ruta = Path(ruta),
                nombre = nombre,
                artista = artista or "",
            )
            for url, ruta, nombre, artista in filas
        ]

    def todas_las_canciones(self) -> List[Cancion]:
        return self.buscar_canciones("")

    @try_sql
    def obtener_cancion(self, url: str) -> Cancion | None:
        if not url: return None

        fila = self.cursor.execute(f"""
                SELECT
                    url,
                    ruta,
                    nombre,
                    artista
                FROM canciones
                WHERE url = ?
            """, (url, )).fetchone()

        if not fila: return None

        url, ruta, nombre, artista = fila

        return Cancion(
                url = url,
                ruta = Path(ruta),
                nombre = nombre,
                artista = artista or "",
            )

    # Modificar tabla

    @try_sql
    def añadir_cancion(self, url: str, nombre: str, ruta: Path, artista: str = "", favorita: bool = False) -> str: # Devuelve el url de la canción
        if self.obtener_cancion(url): return url

        self.cursor.execute("""
            INSERT INTO canciones (
                url,
                nombre,
                ruta,
                artista
            )
            VALUES (?, ?, ?, ?)
        """, (
            url,
            nombre,
            str(ruta),
            artista
        ))

        if favorita: self.marcar_favorita(url)
        self.cursor.execute("DELETE FROM canciones_eliminadas WHERE url = ?", (url, ))

        self.conexion.commit()

        return url

    @try_sql
    def modificar_cancion(self, url: str, nombre: str = None, artista: str = None) -> bool: # Devuelve false si no existe la canción
        if not self.obtener_cancion(url): return False

        campos = []
        valores = []

        if nombre is not None:
            campos.append("nombre = ?")
            valores.append(nombre)

        if artista is not None:
            campos.append("artista = ?")
            valores.append(artista)

        if not campos:
            return False

        valores.append(url)

        self.cursor.execute(
            f"""
                UPDATE canciones
                SET {", ".join(campos)}
                WHERE url = ?
            """,
            valores
        )

        self.conexion.commit()

        return True

    @try_sql
    def eliminar_cancion(self, url: str) -> bool: # Devuelve false si no existe la canción
        if self.obtener_cancion(url) is None: return False

        self.cursor.execute("DELETE FROM canciones WHERE url = ?", (url, ))
        self.cursor.execute("DELETE FROM canciones_playlists WHERE cancion_url = ?", (url, ))
        self.cursor.execute("INSERT OR IGNORE INTO canciones_eliminadas (url) VALUES (?)", (url, ))

        self.conexion.commit()

        return True

    # Favoritas

    def obtener_favoritas(self) -> List[Cancion]:
        return self.obtener_playlist(0).canciones

    def marcar_favorita(self, url: str) -> bool: # Devuelve false si no existe la cancion
        return self.añadir_cancion_playlist(url, 0)

    # ---------------------------------------------------------
    # PLAYLISTS
    # ---------------------------------------------------------

    @try_sql
    def añadir_playlist(self, nombre: str) -> int: # Devuelve el id de la playlist
        cursor = self.cursor.execute("""
            INSERT INTO playlists (nombre)
            VALUES (?)
        """, (nombre, ))

        self.conexion.commit()

        return self.cursor.lastrowid

    @try_sql
    def buscar_playlists(self, busqueda: str) -> List[Playlist]:
        busqueda = f"%{busqueda}%"

        filas = self.cursor.execute("""
            SELECT 
                p.id, 
                p.nombre
            FROM playlists AS p
            WHERE 
                p.nombre LIKE ?
                OR EXISTS (
                    SELECT 1 FROM canciones_playlists AS cp
                    JOIN canciones AS c ON c.url = cp.cancion_url
                    WHERE cp.playlist_id = p.id AND (c.nombre LIKE ? OR c.artista LIKE ?)
                )
        """, (busqueda, busqueda, busqueda)).fetchall()

        playlists = []

        for playlist_id, nombre_playlist in filas:
            filas = self.cursor.execute("""
                SELECT
                    c.url,
                    c.nombre,
                    c.ruta,
                    c.artista
                FROM canciones c
                JOIN canciones_playlists AS cp ON c.url = cp.cancion_url
                WHERE cp.playlist_id = ?
            """, (playlist_id, )).fetchall()

            playlists.append(
                Playlist(
                    id = playlist_id,
                    nombre = nombre_playlist,
                    canciones = [Cancion(
                        url = url,
                        nombre = nombre,
                        ruta = Path(ruta),
                        artista = artista or ""
                    ) for url, nombre, ruta, artista in filas]
                )
            )

        return playlists

    def todas_las_playlists(self) -> List[Playlist]:
        return self.buscar_playlists("")

    @try_sql
    def obtener_playlist(self, playlist_id: int) -> Playlist | None:
        playlist = self.cursor.execute("""
            SELECT id, nombre
            FROM playlists
            WHERE id = ?
        """, (playlist_id, )).fetchone()

        if playlist is None:
            return None

        playlist_id, nombre_playlist = playlist

        filas = self.cursor.execute("""
                SELECT
                    c.url,
                    c.nombre,
                    c.ruta,
                    c.artista
                FROM canciones c
                JOIN canciones_playlists cp ON c.url = cp.cancion_url
                WHERE cp.playlist_id = ?
            """, (playlist_id, )).fetchall()

        return Playlist(
            id=playlist_id,
            nombre=nombre_playlist,
            canciones= [] if not filas else [Cancion(
                url = url,
                nombre = nombre,
                ruta = Path(ruta),
                artista = artista or ""
            ) for url, nombre, ruta, artista in filas]
        )

    # ---------------------------------------------------------
    # CANCIONES_PLAYLISTS
    # ---------------------------------------------------------

    @try_sql
    def añadir_cancion_playlist(self, cancion_url: str, playlist_id: int) -> bool:
        self.cursor.execute("""
            INSERT OR IGNORE INTO canciones_playlists(
                cancion_url,
                playlist_id
            )
            VALUES (?, ?)
        """, (cancion_url, playlist_id))

        self.conexion.commit()
        return True

    @try_sql
    def eliminar_cancion_playlist(self, cancion_url: str, playlist_id: int) -> bool:
        if self.obtener_cancion(cancion_url) is None or self.obtener_playlist(playlist_id) is None:
            return False

        self.cursor.execute("""
            DELETE FROM canciones_playlists
            WHERE cancion_url = ?
            AND playlist_id = ?
        """, (cancion_url, playlist_id))

        self.conexion.commit()

        return True

    # ---------------------------------------------------------
    # PLAYLISTS-YOUTUBE
    # ---------------------------------------------------------

    @try_sql
    def añadir_url_playlist_yt(self, url: str, nombre: str) -> int: # id playlist
        res = self.cursor.execute("""
            SELECT playlist_id
            FROM playlists_youtube
            WHERE playlist_url = ?
        """, (url, )).fetchone()

        if res: return res[0]

        id = self.añadir_playlist(nombre)

        self.cursor.execute("""
            INSERT INTO playlists_youtube (
                playlist_id,
                playlist_url
            )
            VALUES (?, ?)
        """, (id, url))

        self.conexion.commit()

        return id

    @try_sql
    def obtener_playlists_yt(self) -> List[Tuple[int, str]]: # (id, url)
        filas = self.cursor.execute("""
            SELECT playlist_id, playlist_url
            FROM playlists_youtube
        """).fetchall() 

        return filas if filas else []

    # ---------------------------------------------------------
    # CERRAR
    # ---------------------------------------------------------

    def cerrar(self):
        self.conexion.close()





