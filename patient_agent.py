"""
Patient agent — uses GPT-4o to generate realistic patient responses
based on the scenario system prompt and conversation history.
"""

import os
from openai import AsyncOpenAI

client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global client
    if client is None:
        client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return client


async def get_patient_response(
    scenario_system_prompt: str,
    conversation_history: list[dict],
    agent_message: str,
) -> str:
    """
    Given the current scenario and conversation so far, generate the patient's
    next response to what the agent just said.

    conversation_history: list of {"role": "user"|"assistant", "content": str}
      where "user" = agent, "assistant" = patient (from OpenAI's perspective)
    """

    # Build message history for OpenAI:
    # - System prompt describes the patient persona
    # - "user" turns = what the agent said (we're the assistant responding to the agent)
    # - "assistant" turns = what the patient (us) said previously

    messages = [
        {
            "role": "system",
            "content": scenario_system_prompt + """

ADDITIONAL INSTRUCTIONS:
- Keep your response to 1-3 sentences maximum. Sound natural and human.
- Do NOT narrate actions or emotions in brackets like [pauses] or [sighs].
- Do NOT break character or acknowledge you are an AI.
- If the conversation has achieved its goal and it feels natural to end, say a polite goodbye.
- If the agent seems to be wrapping up but you haven't achieved your goal, gently redirect.
- Respond ONLY with what you would say out loud — no stage directions, no internal thoughts.
""",
        }
    ]

    # Add conversation history
    messages.extend(conversation_history)

    # Add the new agent message
    messages.append({"role": "user", "content": agent_message})

    oai = get_client()
    response = await oai.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        max_tokens=150,
        temperature=0.7,
    )

    return response.choices[0].message.content.strip()


async def synthesize_speech(text: str) -> bytes:
    """
    Convert text to speech using OpenAI TTS.
    Returns raw MP3 bytes.
    """
    oai = get_client()
    response = await oai.audio.speech.create(
        model="tts-1",
        voice="alloy",  # Clear, neutral voice appropriate for a patient
        input=text,
        response_format="mp3",
    )
    return response.content


async def should_end_call(text: str) -> bool:
    """
    Heuristic check to see if the patient is saying goodbye / ending the call.
    """
    end_phrases = [
        "goodbye", "bye", "thank you so much", "thanks so much",
        "have a good day", "have a great day", "talk to you later",
        "that's all i needed", "that's everything", "thanks, bye",
        "okay bye", "alright bye", "great, thank you, bye",
    ]
    lower = text.lower()
    return any(phrase in lower for phrase in end_phrases)
