# qna.py

import os
from openai import OpenAI

from .qna_prompt import build_playlist_qna_prompt


MODEL_NAME = "llama-3.1-8b-instant"

client = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1",
)


def answer_playlist_question(
    playlist: dict,
    playlist_summary: dict,
    student_question: str,
) -> str:
    """
    Answers a student's question using playlist-specific context.
    Returns plain text answer.
    """

    prompts = build_playlist_qna_prompt(
        playlist_title=playlist["title"],
        channel_name=playlist.get("channel_title", ""),
        playlist_description=playlist.get("description", ""),
        top_video_titles=playlist.get("top_video_titles", []),
        playlist_summary=playlist_summary,
        student_question=student_question,
    )

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": prompts["system_prompt"]},
            {"role": "user", "content": prompts["user_prompt"]},
        ],
        temperature=0.4,
    )
def start_playlist_chatbot(
    playlist: dict,
    playlist_summary: dict,
):
    """
    Starts a multi-turn, playlist-scoped chatbot session.
    Conversation memory is kept in RAM only.
    """

    # Build the base prompt once
    base_prompts = build_playlist_qna_prompt(
        playlist_title=playlist["title"],
        channel_name=playlist.get("channel_title", ""),
        playlist_description=playlist.get("description", ""),
        top_video_titles=playlist.get("top_video_titles", []),
        playlist_summary=playlist_summary,
        student_question="",
    )

    # Initialize conversation history
    conversation_history = [
        {"role": "system", "content": base_prompts["system_prompt"]},
        {"role": "user", "content": base_prompts["user_prompt"]},
    ]

    print("\nYou can now ask multiple questions about this playlist.")
    print("Type 'exit' to end the chat.\n")

    while True:
        student_question = input("> ").strip()

        if student_question.lower() == "exit":
            print("\nEnding chat.\n")
            break

        conversation_history.append(
            {"role": "user", "content": student_question}
        )

        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=conversation_history,
            temperature=0.4,
        )

        answer = response.choices[0].message.content.strip()

        conversation_history.append(
            {"role": "assistant", "content": answer}
        )

        print("\nANSWER:\n")
        print(answer)
        print("\n---\n")
    return response.choices[0].message.content.strip()