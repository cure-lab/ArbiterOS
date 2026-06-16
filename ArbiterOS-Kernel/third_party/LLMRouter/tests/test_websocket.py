import asyncio
import json
try:
    import websockets
except ImportError:
    print("Please install: pip install websockets")
    exit(1)

async def test_ws():
    uri = "ws://localhost:8000/v1/chat/ws"
    async with websockets.connect(uri) as websocket:
        request = {
            "model": "auto",
            "messages": [
                {"role": "user", "content": "Tell me a short joke."}
            ],
            "stream": True
        }
        
        print(f"Sending request to {uri}...")
        await websocket.send(json.dumps(request))
        
        print("Receiving response...")
        try:
            while True:
                response = await websocket.recv()
                print(f"Received: {response}")
                if "[DONE]" in response:
                    break
        except websockets.exceptions.ConnectionClosedOK:
            print("Connection closed normally")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_ws())
