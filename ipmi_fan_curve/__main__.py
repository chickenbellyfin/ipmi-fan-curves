#!/usr/bin/env python3
import argparse
import logging

import uvicorn

log = logging.getLogger("ipmi-fan")


def main():
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="IPMI Fan Curve Manager")
    parser.add_argument("--mock", action="store_true",
                        help="Use simulated sensors instead of real ipmitool")
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--ipmi-profile", default=None,
                        help="IPMI command profile (auto-detected if omitted). "
                             "Options: supermicro-classic, supermicro-h12")
    args = parser.parse_args()

    from ipmi_fan_curve import server

    if args.mock:
        from ipmi_fan_curve import mock_ipmi as backend_mod
        log.info("Running in MOCK mode — no real IPMI commands will be issued")
    else:
        from ipmi_fan_curve import ipmi as backend_mod
        backend_mod.set_profile(args.ipmi_profile)

    server.backend = backend_mod
    uvicorn.run(server.app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
