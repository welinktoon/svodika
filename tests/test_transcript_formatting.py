"""Transcript layout stays readable without inventing speaker identities."""

from services.transcript_formatting import (
    AI_DIALOGUE_FORMAT_INSTRUCTION,
    append_ai_dialogue_instruction,
    format_segmented_transcript,
    make_plain_transcript_readable,
    transcript_to_markdown_replicas,
)


def test_segmented_transcript_keeps_each_whisper_segment_as_a_replica():
    result = format_segmented_transcript(
        [(0.2, " Всем привет. "), (12.8, " Давайте начнём.")]
    )

    assert result == "[00:00]\nВсем привет.\n\n[00:12]\nДавайте начнём."


def test_untimed_adapter_output_remains_compatible_single_paragraph_text():
    result = format_segmented_transcript(
        [(None, " первая часть "), (None, " вторая часть ")]
    )

    assert result == "первая часть вторая часть"


def test_plain_api_transcript_is_split_into_short_readable_paragraphs():
    result = make_plain_transcript_readable(
        "Первое предложение. Второе предложение. Третье предложение."
    )

    assert result == "Первое предложение. Второе предложение.\n\nТретье предложение."


def test_existing_speaker_lines_are_kept_as_separate_replicas():
    result = make_plain_transcript_readable(
        "Ведущий: Начинаем встречу.\nУчастник: Да, я готов."
    )

    assert result == (
        "Ведущий: Начинаем встречу.\n\nУчастник: Да, я готов."
    )


def test_timestamped_transcript_becomes_distinct_markdown_quotes():
    result = transcript_to_markdown_replicas(
        "[00:03]\nПервая реплика.\n\n[00:09]\nВторая реплика."
    )

    assert "> **[00:03]**" in result
    assert "> Первая реплика." in result
    assert result.count("> **[") == 2


def test_legacy_wall_of_text_is_split_without_inventing_speakers():
    source = " ".join(["длинная старая расшифровка"] * 20)

    result = transcript_to_markdown_replicas(source)

    assert result.count("> ") >= 2
    assert "Говорящий" not in result


def test_ai_dialogue_instruction_is_added_once_to_custom_prompts():
    result = append_ai_dialogue_instruction("Исправь пунктуацию.")

    assert result.count(AI_DIALOGUE_FORMAT_INSTRUCTION) == 1
    assert append_ai_dialogue_instruction(result) == result
