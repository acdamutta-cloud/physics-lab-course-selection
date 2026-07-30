"""兼容入口：实验室演示数据改用安全的幂等同步。

默认仅 dry-run；如需实际同步，请显式传入 ``--apply``。
"""

import asyncio

from scripts.sync_physics_resources import parse_args, run

if __name__ == "__main__":
    arguments = parse_args()
    raise SystemExit(asyncio.run(run(apply=arguments.apply)))
