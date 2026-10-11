"""Customer notifications (Phase 8D).

One contract: notify() records the message in the notifications table and
emits notification.sent through the event pipeline, so consoles see it live
and the customer trail stays queryable at GET /notifications.

A shipment without customer contact details simply produces no notification
- a missing phone number must never break an operational flow.
"""
from events import emit


def notify(conn, shipment_id, kind, message):
    """Record + broadcast one customer message; returns the event dict or
    None when the shipment has no customer contact to notify."""
    shipment = conn.execute(
        "SELECT id, customer_name, customer_phone FROM shipments "
        "WHERE id = %s", (shipment_id,)).fetchone()
    if shipment is None:
        return None
    recipient = shipment["customer_name"] or shipment["customer_phone"]
    if not recipient:
        return None
    row = conn.execute(
        "INSERT INTO notifications (shipment_id, kind, recipient, message) "
        "VALUES (%s, %s, %s, %s) RETURNING id, sent_at",
        (shipment_id, kind, recipient, message)).fetchone()
    return emit(
        conn, "notification.sent", "info", shipment_id=shipment_id,
        details={"notification_id": row["id"], "kind": kind,
                 "recipient": recipient, "message": message})


def assigned_message(shipment, plate):
    return (f"Update on shipment #{shipment['id']}: it is on the way "
            f"to you (vehicle {plate}).")


def delivered_message(shipment_id):
    return (f"Update on shipment #{shipment_id}: it has been delivered. "
            "Thank you for waiting.")


def incident_message(shipment_id, incident_kind):
    return (f"Update on shipment #{shipment_id}: we are aware of a problem "
            f"on the way ({incident_kind}) and are handling it. Your "
            "delivery is being rerouted or rescheduled if needed.")
