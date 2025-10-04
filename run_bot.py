#!/usr/bin/env python3
import asyncio
import logging
import sys
import os

# Добавляем путь к проекту
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from bot import main

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

async def run_bot():
    """Запуск бота с обработкой ошибок"""
    while True:
        try:
            logger.info("Запускаем бота...")
            await main()
        except Exception as e:
            logger.error(f"Ошибка в работе бота: {e}")
            logger.info("Перезапуск через 10 секунд...")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(run_bot())