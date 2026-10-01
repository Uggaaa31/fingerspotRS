import asyncio
import aiomysql
import json
import sys
from datetime import datetime

async def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "get_user_id_list"
    dev_id = sys.argv[2] if len(sys.argv) > 2 else "C2657C94530C2D25"
    param1 = sys.argv[3] if len(sys.argv) > 3 else "3"

    cmd_code = ""
    payload_obj = None
    payload_type = "JSON"

    if action == "get_user_id_list":
        cmd_code = "GET_USER_ID_LIST"
        payload_obj = None
        payload_type = "EMPTY"
    elif action == "get_device_status":
        cmd_code = "GET_DEVICE_STATUS"
        payload_obj = None
        payload_type = "EMPTY"
    elif action == "get_user_info":
        cmd_code = "GET_USER_INFO"
        payload_obj = {"user_id": str(param1)}
    elif action == "get_enroll_data":
        cmd_code = "GET_ENROLL_DATA"
        payload_obj = {"user_id": str(param1), "backup_number": int(sys.argv[4]) if len(sys.argv) > 4 else 0}
    elif action == "set_time":
        cmd_code = "SET_TIME"
        now_str = datetime.now().strftime("%Y%m%d%H%M%S")
        payload_obj = {"time": now_str}
    else:
        cmd_code = action
        payload_obj = None
        payload_type = "EMPTY"

    payload_str = json.dumps(payload_obj) if payload_obj is not None else None

    conn = await aiomysql.connect(
        host="db", port=3306, user="adms_user", password="adms_password", db="presensi_local"
    )
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO adms_commands (device_sn, cmd_code, payload, payload_type, status, expires_at)
            VALUES (%s, %s, %s, %s, 'PENDING', NOW() + INTERVAL 1 HOUR)
            """,
            (dev_id, cmd_code, payload_str, payload_type)
        )
        await conn.commit()
        print(f"Queued command ID={cur.lastrowid} Code={cmd_code} Dev={dev_id} Payload={payload_str}")
    conn.close()

if __name__ == "__main__":
    asyncio.run(main())
