"""Entry point: `python main.py` starts a local server and opens the map in your browser.

The old Tkinter window is still available with `python -m mapa_polski.app`.
"""
import argparse
import threading
import webbrowser

from mapa_polski.server import create_server


def main():
    parser = argparse.ArgumentParser(description="Mapa Polski – pociągi z rozkładu na mapie Leaflet/OSM")
    parser.add_argument("--port", type=int, default=None, help="port serwera (domyślnie pierwszy wolny od 8765)")
    parser.add_argument("--no-browser", action="store_true", help="nie otwieraj przeglądarki automatycznie")
    args = parser.parse_args()

    server, url = create_server(args.port)
    print(f"Mapa Polski działa pod {url}  (Ctrl+C kończy)", flush=True)
    if not args.no_browser:
        threading.Timer(0.6, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nZamykanie serwera…")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
