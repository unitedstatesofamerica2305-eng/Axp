# 🎁 Advanced Giveaway & Voting Bot

A fully automated, fair, and feature-rich giveaway system for Telegram. This bot supports organic voting, premium memberships, paid votes (via Stars or INR), and robust force-join (FSub) verification to help grow your channels.

---

## **🛠️ Admin & Configuration Commands**

### **💬 Custom Text & Formatting Commands (SetText)**
* **/setstart** - Sets a custom start message for the bot.
* **/resetstart** - Resets the start message back to the default.
* **/setwin [text]** - Sets the global win message template. You must include the `{winners}` placeholder in your text.
* **/setvotetext [text]** - Sets the text caption for participation/vote posts.
* **/setmemtext** - Sets the premium membership menu text. It must include the `{status}` placeholder.
* **/setposttext [text]** - Sets the welcome message sent to a user when the bot is added to a channel as an admin. Supports variables like `{channel}`, `{user}`, and `{link}`.

### **⚙️ General Admin & Management Commands**
* **/stats** - Displays bot statistics including total users, active giveaways, and top creators.
* **/broadcast** - Reply to a message with this command to broadcast it to all users of the bot.
* **/setjoin** - Opens the menu to manage global force-join channels (allows up to 10 channels).
* **/resync {ga_id}** - Manually validates voters for a specific giveaway and removes invalid votes if users left the required channels.
* **/createpost** - Launches an interactive tool to create a custom post (with media, caption, and inline buttons) and publish it to connected channels.
* **/support** - Displays the admin and support contact information.

### **💎 Premium & Paid Settings Commands**
* **/setprices [days] [price]...** - Sets the premium membership plans. Example: `/setprices 1D 20 7D 70 30D 80`.
* **/setqr** - Reply to a photo with this command to set it as the official payment QR code for membership purchases.
* **/gift** - Prompts you to enter a User ID to gift them a free premium membership duration.
* **/conmembership** - Lists all users with active premium memberships and allows admins to view details or cancel them.

---

## **🚀 Additional Deployment Guide**

Follow these steps to configure and deploy your bot properly:

1. **Locate `bot.py`**: Open the main script file.
2. **Edit Variables**: Update your core credentials and variables from **Line 35 to 42**.
3. **Edit Start Message**: Modify the default start text around **Lines 475-476**.
4. **Update 'How to Use'**: Change the tutorial/support link on **Line 485**.
5. **Set Admin Logs**: Update your specific log ID on **Line 1351**.
6. **Update Support IDs**: Change the support contact IDs on **Line 2700**.
7. **Configure Database**: Open `database.py` and insert your `mongo_db` connection string.
8. **Update Config**: Edit any remaining environment variables in `config.py`.

### **🌐 Quick Deploy (Heroku)**

Use the link below to deploy directly to Heroku. *(Ensure you replace `{github_username}` and `{repo}` in the URL with your actual GitHub repository details before clicking).*

**Deploy Link:** `https://dashboard.heroku.com/new?template=https://github.com/{github_username}/{repo}`
