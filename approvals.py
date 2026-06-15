def approval_card(text, action_id):
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {"type": "TextBlock", "text": text, "wrap": True}
                    ],
                    "actions": [
                        {
                            "type": "Action.Submit",
                            "title": "Approve",
                            "data": {"action": "approve", "action_id": action_id}
                        },
                        {
                            "type": "Action.Submit",
                            "title": "Reject",
                            "data": {"action": "reject", "action_id": action_id}
                        }
                    ]
                }
            }
        ]
    }