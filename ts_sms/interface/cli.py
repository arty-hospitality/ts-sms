"""``ts-sms-execute``: submit a single request through TenantSageGateway and print the result."""

import argparse
import asyncio
import json
import sys

from ts_sms.interface.gateway import TenantSageGateway


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit a request to the TS-SMS state machine.")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--target", default=None, help="Temporal address (overrides TS_SMS_TEMPORAL_ADDRESS)")
    args = parser.parse_args()

    gateway = TenantSageGateway(temporal_target=args.target)
    result = asyncio.run(gateway.execute(args.user_id, args.tenant_id, args.query))
    print(json.dumps(result, indent=2, sort_keys=True))
    sys.exit(0 if result.get("status") == "ALLOW" else 1)


if __name__ == "__main__":
    main()
