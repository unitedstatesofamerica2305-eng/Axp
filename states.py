from aiogram.fsm.state import State, StatesGroup

# Giveaway Creation
class CreateGiveaway(StatesGroup):
    waiting_for_description = State()
    waiting_for_target_channel = State()
    waiting_for_force_channels = State()
    # New Paid Vote States
    waiting_for_paid_confirm = State()
    waiting_for_payment_methods = State()
    waiting_for_upi_qr = State()
    waiting_for_upi_id = State()
    waiting_for_upi_rate = State()
    waiting_for_star_username = State()
    waiting_for_star_rate = State()

# Broadcast
class BroadcastState(StatesGroup):
    waiting_for_message = State()

# Paid Vote Process (User Side)
class BuyVotes(StatesGroup):
    waiting_for_screenshot = State()
    waiting_for_amount = State()
    waiting_for_star_count = State()

# Membership System (Admin Side)
class AdminSettings(StatesGroup):
    waiting_for_prices = State()
    waiting_for_qr = State()

# Membership System (User Side)
class BuyMembership(StatesGroup):
    waiting_for_payment_proof = State()
    waiting_for_channel_link = State()
  
