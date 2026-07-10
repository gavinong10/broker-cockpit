from app.notify import discord_message

def test_discord_message_shape():
    body = discord_message("gateway.disconnect", "IB Gateway disconnected")
    assert body["embeds"][0]["title"] == "gateway.disconnect"
    assert "disconnected" in body["embeds"][0]["description"]
