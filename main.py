import os
import json
import base64
import asyncio
import websockets
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
import requests
import json
from twilio.rest import Client
# from dotenv import load_dotenv

# load_dotenv()

# Configuration
# OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
OPENAI_API_KEY ='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' 
PORT = int(os.getenv('PORT', 5050))
SYSTEM_MESSAGE = (
    "You are Raghini from Realtime Currency Exchange Services. "
    "You communicate primarily in Hindi but can switch to English if the user speaks in English. "
    "Your role is to assist users with two specific tasks. If the user discusses topics outside these tasks, gently redirect the conversation back to the relevant tasks.\n\n"
    "Your two primary tasks are:\n\n"
    "1. **Send a Code Red Message via WhatsApp:**\n"
    "   - When a user indicates they want to send a Code Red message, ask for their email ID.\n"
    "   - After capturing the email ID, confirm it with the user. Spell out the email word by word for confirmation. For example, if the email is 'himanshu6513@gmail.com,' confirm it as 'h-i-m-a-n-s-h-u-6-5-1-3-@-g-m-a-i-l-.-c-o-m.'\n"
    "   - If the user disagrees with the captured email, politely ask them to provide the email ID word by word, spelling out each character clearly.\n"
    "   - Repeat this process until the user explicitly confirms the email ID as correct.\n"
    "   - Once the email ID is confirmed, ask the user for the message they want to send. Accept the message in any language, translate it into English, and trigger the function call `send_whatsapp_message(email, message)` with the confirmed email and translated message.\n\n"
    "2. **Currency Conversion:**\n"
    "   - Assist users with currency conversion by triggering the function `currency_exchange(base_currency, target_currency, amount)`.\n"
    "   - Automatically interpret user input to identify the base currency, target currency, and amount, even if described in natural language (e.g., 'Indian currency' → INR, 'Japanese currency' → JPY, 'Portuguese currency' → EUR).\n"
    "   - For example, if a user says, 'How much Indian currency can I get for 45 American dollars?' infer and execute the function call as `currency_exchange(USD, INR, 45)`.\n"
    "   - Similarly, adapt inputs like 'Convert 100 euros to Japanese yen' or 'What is the value of 50 British pounds in Australian dollars?' into the corresponding function call.\n"
    "   - Provide responses in the user’s preferred language and style, ensuring clear and user-friendly communication.\n\n"
    "**Function Details:**\n"
    "- `send_whatsapp_message(email, message)` sends a WhatsApp message with the specified email and message.\n"
    "- `currency_exchange(base_currency, target_currency, amount)` converts the specified amount from the base currency to the target currency and provides the conversion result.\n\n"
    "**Additional Notes:**\n"
    "- If users reference currencies without standard symbols, automatically map their terms to the correct currency symbols based on context.\n"
    "- Clarify ambiguities if needed, but aim for efficient and intuitive responses.\n"
    "- Your primary objective is to stay focused on these tasks and ensure the user’s requirements are addressed clearly and efficiently.\n\n"
    "If the user deviates from these tasks, gently redirect them to either sending a WhatsApp message or performing a currency conversion. Always respond in Hindi by default unless the user initiates communication in English or explicitly prefers English."
)

VOICE = 'alloy'
LOG_EVENT_TYPES = [
    'error', 'response.content.done', 'rate_limits.updated',
    'response.done', 'input_audio_buffer.committed',
    'input_audio_buffer.speech_stopped', 'input_audio_buffer.speech_started',
    'session.created'
]
SHOW_TIMING_MATH = True

app = FastAPI()

if not OPENAI_API_KEY:
    raise ValueError('Missing the OpenAI API key. Please set it in the .env file.')

@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Twilio Media Stream Server is running!"}

@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """Handle incoming call and return TwiML response to connect to Media Stream."""
    response = VoiceResponse()
    # <Say> punctuation to improve text-to-speech flow
    # response.say("Please wait while we connect your call to the A. I. voice assistant, powered by Twilio and the Open-A.I. Realtime API")
    # response.pause(length=1)
    # response.say("O.K. you can start talking!")
    host = request.url.hostname
    connect = Connect()
    connect.stream(url=f'wss://{host}/media-stream')
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")

@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """Handle WebSocket connections between Twilio and OpenAI."""
    print("Client connected")
    await websocket.accept()

    async with websockets.connect(
        'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17',
        extra_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1"
        }
    ) as openai_ws:
        await initialize_session(openai_ws)

        # Connection specific state
        stream_sid = None
        latest_media_timestamp = 0
        last_assistant_item = None
        mark_queue = []
        response_start_timestamp_twilio = None
        
        async def receive_from_twilio():
            """Receive audio data from Twilio and send it to the OpenAI Realtime API."""
            nonlocal stream_sid, latest_media_timestamp
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    if data['event'] == 'media' and openai_ws.open:
                        latest_media_timestamp = int(data['media']['timestamp'])
                        audio_append = {
                            "type": "input_audio_buffer.append",
                            "audio": data['media']['payload']
                        }
                        await openai_ws.send(json.dumps(audio_append))
                    elif data['event'] == 'start':
                        stream_sid = data['start']['streamSid']
                        print(f"Incoming stream has started {stream_sid}")
                        response_start_timestamp_twilio = None
                        latest_media_timestamp = 0
                        last_assistant_item = None
                    elif data['event'] == 'mark':
                        if mark_queue:
                            mark_queue.pop(0)
            except WebSocketDisconnect:
                print("Client disconnected.")
                if openai_ws.open:
                    await openai_ws.close()

        async def send_to_twilio():
            """Receive events from the OpenAI Realtime API, send audio back to Twilio."""
            nonlocal stream_sid, last_assistant_item, response_start_timestamp_twilio
            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)
                    await handle_tool_invocation(response, openai_ws)
                    if response['type'] in LOG_EVENT_TYPES:
                       print(f"Received event: {response['type']}", response)
                    # if response.get('type') == 'response.done':
                    #     transcription = response['response']['output'][0]['content'][0]['transcript']
                    #     if transcription:
                    #         print(f"AI said: {transcription}")
                    if response.get('type') == 'response.audio.delta' and 'delta' in response:
                        audio_payload = base64.b64encode(base64.b64decode(response['delta'])).decode('utf-8')
                        audio_delta = {
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {
                                "payload": audio_payload
                            }
                        }
                        await websocket.send_json(audio_delta)

                        if response_start_timestamp_twilio is None:
                            response_start_timestamp_twilio = latest_media_timestamp
                            if SHOW_TIMING_MATH:
                                print(f"Setting start timestamp for new response: {response_start_timestamp_twilio}ms")

                        # Update last_assistant_item safely
                        if response.get('item_id'):
                            last_assistant_item = response['item_id']

                        await send_mark(websocket, stream_sid)

                    # Trigger an interruption. Your use case might work better using `input_audio_buffer.speech_stopped`, or combining the two.
                    if response.get('type') == 'input_audio_buffer.speech_started':
                        #print("Speech started detected.")
                        if last_assistant_item:
                           # print(f"Interrupting response with id: {last_assistant_item}")
                            await handle_speech_started_event()
            except Exception as e:
                print(f"Error in send_to_twilio: {e}")

        async def handle_speech_started_event():
            """Handle interruption when the caller's speech starts."""
            nonlocal response_start_timestamp_twilio, last_assistant_item
           # print("Handling speech started event.")
            if mark_queue and response_start_timestamp_twilio is not None:
                elapsed_time = latest_media_timestamp - response_start_timestamp_twilio
                if SHOW_TIMING_MATH:
                    print(f"Calculating elapsed time for truncation: {latest_media_timestamp} - {response_start_timestamp_twilio} = {elapsed_time}ms")

                if last_assistant_item:
                    if SHOW_TIMING_MATH:
                        print(f"Truncating item with ID: {last_assistant_item}, Truncated at: {elapsed_time}ms")

                    truncate_event = {
                        "type": "conversation.item.truncate",
                        "item_id": last_assistant_item,
                        "content_index": 0,
                        "audio_end_ms": elapsed_time
                    }
                    await openai_ws.send(json.dumps(truncate_event))

                await websocket.send_json({
                    "event": "clear",
                    "streamSid": stream_sid
                })

                mark_queue.clear()
                last_assistant_item = None
                response_start_timestamp_twilio = None

        async def send_mark(connection, stream_sid):
            if stream_sid:
                mark_event = {
                    "event": "mark",
                    "streamSid": stream_sid,
                    "mark": {"name": "responsePart"}
                }
                await connection.send_json(mark_event)
                mark_queue.append('responsePart')

        await asyncio.gather(receive_from_twilio(), send_to_twilio())

async def send_initial_conversation_item(openai_ws):
    """Send initial conversation item if AI talks first."""
    initial_conversation_item = {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "Greet the user with 'नमस्ते! मैं राघिनी, रीयलटाइम करेंसी एक्सचेंज सर्विसेज से हूँ। मैं आपकी सभी जरूरतों में मदद करने के लिए यहाँ हूँ। मैं आपकी कैसे मदद कर सकती हूँ?'"

                }
            ]
        }
    }
    await openai_ws.send(json.dumps(initial_conversation_item))
    await openai_ws.send(json.dumps({"type": "response.create"}))
async def handle_tool_invocation(response, openai_ws):
    """Handle tool invocation requests from OpenAI."""
    if response.get('type') == 'response.done':
        # Extract the output, which is a list of dictionaries
        output = response.get('response', {}).get('output', [])
        
        # Iterate over each dictionary in the output list
        for item in output:
            if item.get('type') == 'function_call':
                # Extract function call details
                function_call = item
                function_name = function_call.get('name')
                arguments = json.loads(function_call.get('arguments', '{}'))
                call_id = function_call.get('call_id')
                
                if function_name == 'currency_exchange':
                    # Handle currency_exchange function
                    base_currency = arguments.get('base_currency')
                    target_currency = arguments.get('target_currency')
                    amount = arguments.get('amount')
                    
                    if base_currency and target_currency and amount:
                        exchange_info = currency_exchange(base_currency, target_currency, amount)
                        
                        # Prepare and send the response
                        tool_response = {
                            "type": "conversation.item.create",
                            "item": {
                                "type": "function_call_output",
                                "call_id": call_id,
                                "output": exchange_info
                            }
                        }
                        await openai_ws.send(json.dumps(tool_response))
                        await openai_ws.send(json.dumps({"type": "response.create"}))
                
                elif function_name == 'send_whatsapp_message':
                    # Handle send_whatsapp_message function
                    email = arguments.get('email')
                    message = arguments.get('message')
                    
                    if email and message:
                        try:
                            send_whatsapp_message(email, message)
                            whatsapp_response = f"Message successfully sent to WhatsApp with email: {email}"
                        except Exception as e:
                            whatsapp_response = f"Failed to send message: {str(e)}"
                        
                        # Prepare and send the response
                        tool_response = {
                            "type": "conversation.item.create",
                            "item": {
                                "type": "function_call_output",
                                "call_id": call_id,
                                "output": whatsapp_response
                            }
                        }
                        print("Hello", email, message)
                        await openai_ws.send(json.dumps(tool_response))


def send_whatsapp_message(email,message):
  account_sid = 'ACc3b466139e779e862c4f545bd6e19d94'
  auth_token = '0587b58274800f397550c85b621ab921'
  client = Client(account_sid, auth_token)
  try:
    client.messages.create(
                from_="whatsapp:+919319837618",
                body=email+':   '+message,
                to="whatsapp:+918319637167"
            )
  except Exception as e:
    print("Issue in sending message")

def currency_exchange(base_currency,target_currency,amount):
  access_key = "6128b2061e0b16fbfa11c4f52fe4aa20"
  url = f"https://api.exchangerate.host/convert?from={base_currency}&to={target_currency}&amount={amount}&access_key={access_key}"
  response = requests.get(url)
  if response.status_code == 200:
    try:
        data = response.json()
        rate = data.get("result")
        if rate:
            return f"Exchange rate from {base_currency} to {target_currency} for {amount} {base_currency}: {rate} {target_currency}"
        else:
            return "Could not retrieve exchange rate. Check the currency codes or API response."
    except Exception as e:
      return "There is some connection or formatting issue"
  else:
    return "Failed to fetch data"


async def initialize_session(openai_ws):
    """Control initial session with OpenAI."""
    session_update = {
        "type": "session.update",
        "session": {
            "turn_detection": {"type": "server_vad"},
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "voice": VOICE,
            "instructions": SYSTEM_MESSAGE,
            "modalities": ["text", "audio"],
            "temperature": 0.8,
            "tools":  [
                {
                    "type": "function",
                    "name": "currency_exchange",
                    "description": "Converts a given amount from one currency to another using real-time exchange rates.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "base_currency": {"type": "string", "description": "The currency to convert from (e.g., 'USD')."},
                            "target_currency": {"type": "string", "description": "The currency to convert to (e.g., 'INR')."},
                            "amount": {"type": "number", "description": "The amount to be converted."}
                        },
                        "required": ["base_currency", "target_currency", "amount"]
                    }
                },
                {
                    "type": "function",
                    "name": "send_whatsapp_message",
                    "description": "Sends a WhatsApp message to a specified recipient.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "email": {"type": "string", "description": "The sender's email address."},
                            "message": {"type": "string", "description": "The message content to send."}
                        },
                        "required": ["email", "message"]
                    }
                }
            ],
        "tool_choice": "auto"
        }
    }
  #  print('Sending session update:', json.dumps(session_update))
    await openai_ws.send(json.dumps(session_update))

    # Uncomment the next line to have the AI speak first
    await send_initial_conversation_item(openai_ws)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
