#novel_generator/blueprint.py
# -*- coding: utf-8 -*-
"""
章节蓝图生成（Chapter_blueprint_generate 及辅助函数）
"""
import os
import re
import logging
from novel_generator.common import invoke_with_cleaning
from llm_adapters import create_llm_adapter
from prompt_definitions import chapter_blueprint_prompt, chunked_chapter_blueprint_prompt
from utils import read_file, clear_file_content, save_string_to_txt
logging.basicConfig(
    filename='app.log',      # 日志文件名
    filemode='a',            # 追加模式（'w' 会覆盖）
    level=logging.INFO,      # 记录 INFO 及以上级别的日志
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
def compute_chunk_size(number_of_chapters: int, max_tokens: int) -> int:
    """
    基于“每章约100 tokens”的粗略估算，
    再结合当前max_tokens，计算分块大小：
      chunk_size = (floor(max_tokens/100/10)*10) - 10
    并确保 chunk_size 不会小于1或大于实际章节数。
    """
    tokens_per_chapter = 600.0
    ratio = max_tokens / tokens_per_chapter
    ratio_rounded_to_10 = int(ratio // 10) * 10
    chunk_size = ratio_rounded_to_10 - 10
    if chunk_size < 1:
        chunk_size = 1
    if chunk_size > number_of_chapters:
        chunk_size = number_of_chapters
    return chunk_size

def limit_chapter_blueprint(blueprint_text: str, limit_chapters: int = 100) -> str:
    """
    从已有章节目录中只取最近的 limit_chapters 章，以避免 prompt 超长。
    """
    pattern = r"(第\s*\d+\s*章.*?)(?=第\s*\d+\s*章|$)"
    chapters = re.findall(pattern, blueprint_text, flags=re.DOTALL)
    if not chapters:
        return blueprint_text
    if len(chapters) <= limit_chapters:
        return blueprint_text
    selected = chapters[-limit_chapters:]
    return "\n\n".join(selected).strip()

def Chapter_blueprint_generate(
    interface_format: str,
    api_key: str,
    base_url: str,
    llm_model: str,
    filepath: str,
    number_of_chapters: int,
    user_guidance: str = "",  # 新增参数
    temperature: float = 0.7,
    max_tokens: int = 4096,
    timeout: int = 600
) -> None:
    """
    若 Novel_directory.txt 已存在且内容非空，则表示可能是之前的部分生成结果；
      解析其中已有的章节数，从下一个章节继续分块生成；
      对于已有章节目录，传入时仅保留最近100章目录，避免prompt过长。
    否则：
      - 若章节数 <= chunk_size，直接一次性生成
      - 若章节数 > chunk_size，进行分块生成
    生成完成后输出至 Novel_directory.txt。
    """
    arch_file = os.path.join(filepath, "Novel_architecture.txt")
    if not os.path.exists(arch_file):
        logging.warning("Novel_architecture.txt not found. Please generate architecture first.")
        return

    architecture_text = read_file(arch_file).strip()
    if not architecture_text:
        logging.warning("Novel_architecture.txt is empty.")
        return

    llm_adapter = create_llm_adapter(
        interface_format=interface_format,
        base_url=base_url,
        model_name=llm_model,
        api_key=api_key,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout
    )

    filename_dir = os.path.join(filepath, "Novel_directory.txt")
    if not os.path.exists(filename_dir):
        open(filename_dir, "w", encoding="utf-8").close()

    existing_blueprint = read_file(filename_dir).strip()
    chunk_size = compute_chunk_size(number_of_chapters, max_tokens)
    logging.info(f"Number of chapters = {number_of_chapters}, computed chunk_size = {chunk_size}.")

    if existing_blueprint:
        logging.info("Detected existing blueprint content. Will resume chunked generation from that point.")
        pattern = r"(?:第\s*(\d+)\s*章|제\s*(\d+)\s*장)"
        existing_chapter_numbers = [int(a or b) for a, b in re.findall(pattern, existing_blueprint)]
        max_existing_chap = max(existing_chapter_numbers) if existing_chapter_numbers else 0
        logging.info(f"Existing blueprint indicates up to chapter {max_existing_chap} has been generated.")
        final_blueprint = existing_blueprint
        current_start = max_existing_chap + 1
        while current_start <= number_of_chapters:
            current_end = min(current_start + chunk_size - 1, number_of_chapters)
            limited_blueprint = limit_chapter_blueprint(final_blueprint, 100)
            chunk_prompt = chunked_chapter_blueprint_prompt.format(
                novel_architecture=architecture_text,
                chapter_list=limited_blueprint,
                number_of_chapters=number_of_chapters,
                n=current_start,
                m=current_end,
                user_guidance=user_guidance  # 新增参数
            )
            logging.info(f"Generating chapters [{current_start}..{current_end}] in a chunk...")
            chunk_result = invoke_with_cleaning(llm_adapter, chunk_prompt)
            if not chunk_result.strip():
                logging.warning(f"Chunk generation for chapters [{current_start}..{current_end}] is empty.")
                clear_file_content(filename_dir)
                save_string_to_txt(final_blueprint.strip(), filename_dir)
                return
            final_blueprint += "\n\n" + chunk_result.strip()
            clear_file_content(filename_dir)
            save_string_to_txt(final_blueprint.strip(), filename_dir)
            current_start = current_end + 1

        logging.info("All chapters blueprint have been generated (resumed chunked).")
        return

    if chunk_size >= number_of_chapters:
        prompt = chapter_blueprint_prompt.format(
            novel_architecture=architecture_text,
            number_of_chapters=number_of_chapters,
            user_guidance=user_guidance  # 新增参数
        )
        blueprint_text = invoke_with_cleaning(llm_adapter, prompt)
        if not blueprint_text.strip():
            logging.warning("Chapter blueprint generation result is empty.")
            return

        clear_file_content(filename_dir)
        save_string_to_txt(blueprint_text, filename_dir)
        logging.info("Novel_directory.txt (chapter blueprint) has been generated successfully (single-shot).")
        return

    logging.info("Will generate chapter blueprint in chunked mode from scratch.")
    final_blueprint = ""
    current_start = 1
    while current_start <= number_of_chapters:
        current_end = min(current_start + chunk_size - 1, number_of_chapters)
        limited_blueprint = limit_chapter_blueprint(final_blueprint, 100)
        chunk_prompt = chunked_chapter_blueprint_prompt.format(
            novel_architecture=architecture_text,
            chapter_list=limited_blueprint,
            number_of_chapters=number_of_chapters,
            n=current_start,
            m=current_end,
            user_guidance=user_guidance  # 新增参数
        )
        logging.info(f"Generating chapters [{current_start}..{current_end}] in a chunk...")
        chunk_result = invoke_with_cleaning(llm_adapter, chunk_prompt)
        if not chunk_result.strip():
            logging.warning(f"Chunk generation for chapters [{current_start}..{current_end}] is empty.")
            clear_file_content(filename_dir)
            save_string_to_txt(final_blueprint.strip(), filename_dir)
            return
        if final_blueprint.strip():
            final_blueprint += "\n\n" + chunk_result.strip()
        else:
            final_blueprint = chunk_result.strip()
        clear_file_content(filename_dir)
        save_string_to_txt(final_blueprint.strip(), filename_dir)
        current_start = current_end + 1

    logging.info("Novel_directory.txt (chapter blueprint) has been generated successfully (chunked).")
