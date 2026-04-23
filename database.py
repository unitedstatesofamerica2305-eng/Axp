from motor.motor_asyncio import AsyncIOMotorClient

# CONFIGURATION
MONGO_URI = "mongodb+srv://unitedstatesofamerica2305_db_user:xzUhE91EUF8Zfi3l@cluster0.tu7bg5z.mongodb.net/?appName=Cluster0"

# Initialize Client
client = AsyncIOMotorClient(MONGO_URI)
db = client['vespyr_votebot']

# Collections
giveaways_col = db['giveaways']
votes_col = db['votes']
participants_col = db['participants']
users_col = db['users']

# NEW COLLECTIONS
transactions_col = db['transactions']     # Stores pending paid vote/membership requests
memberships_col = db['memberships']       # Stores active force-join channels
settings_col = db['settings']             # Stores admin prices and QR codes
