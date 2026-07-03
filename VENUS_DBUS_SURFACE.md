# Venus EVCS DBus Surface

The DBus gateway is the only production component that may talk to Victron
DBus. The rest of the project must still preserve the Venus EV charger surface:
the GUI and VRM identify the service through specific paths, writeability, and
value meanings.

This is the boundary:

- Gateway responsibility: transport, registration, rate limiting, writes,
  reads, discovery, introspection, health, and cache files.
- Core responsibility: publish a coherent EV charger model with the path
  semantics Venus expects.

The public contract lives in `venus_evcharger/dbus_gateway_surface.py` and is
exported through `venus_evcharger.dbus_gateway`. It records the required
GUI/VRM-visible identity, measurement, status, and control paths. Bootstrap
tests assert that those paths are registered and that writable paths stay
writable only where intended.

Important examples:

- `/Mode`, `/StartStop`, `/Enable`, `/SetCurrent`, and `/AutoStart` are user
  control paths. The gateway carries the writes, but the core defines their
  policy meaning.
- `/Ac/Power`, `/Ac/Current`, `/Ac/Voltage`, `/Ac/Energy/Forward`,
  `/Session/Energy`, and `/Session/Time` feed the EVCS tile and VRM history.
- `/Connected`, `/DeviceInstance`, `/ProductId`, `/ProductName`, and `/Status`
  determine whether Venus recognizes and renders the service as an EV charger.

Do not add direct DBus access to satisfy a GUI requirement. Add or adjust the
surface contract, publish the value through the gateway proxy, and cover the
path with bootstrap or gateway tests.
