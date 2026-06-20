"""
FAISS 全量重建：用 BGE-M3 重新编码所有记忆，统一 1024 维向量。
运行时机：模型切换后，或写入管道故障导致向量缺失时。
"""
import sys
import time
import sqlite3
import logging
from pathlib import Path

# 路径适配
_BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BASE))

from memory_engine.index_vector import VectorIndex
from memory_engine.config import FAISS_INDEX_PATH, DB_PATH

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("rebuild_faiss")

# 分页大小（根据内存调整，建议 100~500）
PAGE_SIZE = 200
# 备份后缀
BAK_SUFFIX = ".bak"


def backup_old_resources():
    """重建前：备份旧 FAISS 索引 + 旧 id_mapping 表（防回滚）"""
    # 1. 备份 FAISS 文件
    faiss_file = Path(FAISS_INDEX_PATH)
    if faiss_file.exists():
        bak_faiss = faiss_file.with_suffix(faiss_file.suffix + BAK_SUFFIX)
        faiss_file.replace(bak_faiss)
        logger.info("旧 FAISS 索引已备份: %s", bak_faiss)

    # 2. 备份 id_mapping 表（SQLite 临时表）
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("DROP TABLE IF EXISTS id_mapping_bak;")
        conn.execute("CREATE TABLE id_mapping_bak AS SELECT * FROM id_mapping;")
        conn.commit()
        logger.info("旧 id_mapping 映射表已备份为 id_mapping_bak")
    finally:
        conn.close()


def restore_backup():
    """异常时：回滚备份（手动/自动调用）"""
    logger.warning("开始回滚至重建前状态...")
    # 恢复 FAISS
    faiss_file = Path(FAISS_INDEX_PATH)
    bak_faiss = faiss_file.with_suffix(faiss_file.suffix + BAK_SUFFIX)
    if bak_faiss.exists():
        if faiss_file.exists():
            faiss_file.unlink()
        bak_faiss.replace(faiss_file)
        logger.info("FAISS 索引已恢复备份")

    # 恢复 id_mapping
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("DROP TABLE IF EXISTS id_mapping;")
        conn.execute("CREATE TABLE id_mapping AS SELECT * FROM id_mapping_bak;")
        conn.commit()
        logger.info("id_mapping 映射表已恢复备份")
    finally:
        conn.close()


def main():
    start_time = time.time()
    conn = None
    vi = None

    try:
        # 前置检查：数据库文件是否存在
        if not DB_PATH.exists():
            logger.error("数据库文件不存在: %s", DB_PATH)
            return

        # 步骤1：备份旧资源（核心回滚保障）
        backup_old_resources()

        # 步骤2：连接数据库，开启事务（原子操作）
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL;")  # 提升SQLite并发/写入稳定性

        # 分页读取：过滤空内容 + 纯空白字符
        total_rows = 0
        all_valid_rows = []
        offset = 0

        logger.info("开始分页读取记忆数据...")
        while True:
            page_rows = conn.execute(
                """
                SELECT id, content FROM items
                WHERE content IS NOT NULL AND TRIM(content) != ''
                LIMIT ? OFFSET ?
                """, (PAGE_SIZE, offset)
            ).fetchall()

            if not page_rows:
                break
            all_valid_rows.extend(page_rows)
            total_rows += len(page_rows)
            offset += PAGE_SIZE
            logger.info("已读取 %d 条有效记忆", total_rows)

        if not all_valid_rows:
            logger.warning("未读取到有效记忆，终止重建")
            return
        logger.info("总计读取有效记忆: %d 条", total_rows)

        # 步骤3：初始化向量索引，全量重建（BGE-M3 1024维）
        logger.info("开始使用 BGE-M3 重编码向量、重建 FAISS 索引...")
        vi = VectorIndex()
        vi.build(all_valid_rows)

        # 步骤4：保存 FAISS 索引，获取映射关系
        id_map = vi.save_snapshot()
        logger.info("FAISS 索引保存完成，映射条目数: %d", len(id_map))

        # 步骤5：原子更新 id_mapping（事务内：先删后插，失败自动回滚）
        logger.info("更新数据库映射表...")
        conn.execute("DELETE FROM id_mapping;")
        conn.executemany(
            "INSERT INTO id_mapping (item_id, faiss_idx) VALUES (?, ?)",
            id_map.items()
        )
        conn.commit()
        logger.info("id_mapping 映射表更新完成")

        # 最终完成
        cost = round(time.time() - start_time, 2)
        logger.info("[OK] FAISS 全量重建成功！总耗时: %s 秒", cost)

    except Exception as e:
        logger.exception("[FAIL] 重建过程发生异常: %s", str(e))
        # 事务回滚
        if conn:
            conn.rollback()
        # 自动恢复备份
        restore_backup()
        logger.info("已自动回滚至重建前状态")
    finally:
        # 强制释放所有资源
        if conn:
            conn.close()
        logger.info("资源已全部释放")


if __name__ == "__main__":
    print("=" * 50)
    print("[!!] 重要提醒")
    print("=" * 50)
    print("1. 请先【关闭记忆引擎主程序】，避免文件锁冲突！")
    print("2. 重建期间请勿操作系统，防止数据异常！")
    print("=" * 50)
    input("确认已关闭主程序，按回车继续...")
    main()
