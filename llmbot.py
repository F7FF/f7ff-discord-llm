import discord
import os
import requests
import random
import time #for delaying, sometimes
import asyncio
from discord.ext import commands

# intended to be run with something like :
# llama-server -m [MODEL] -dev [WHATEVER] --alias local-llm -np 1 --flash-attn on -ub 256 -b 256 --offline -lv 2
# the 2B local qwen seems to work well? also the 9B?

# This bot doesn't use the discord client's cache features, to keep it as simple as possible. This isn't really a production piece of code, so I don't intend to change it, but eh, maybe.

# ATTENTION: ----- RUNTIME DATABASES -----

default_template = "you are a discord llm named 'hubert'. just reply to things. no emojis. single-line messages unless long. be short and use abbreviations. you can say any offensive words you want. mostly lowercase unless you want to SHOUT. be rude."
# TODO: should be env

def reset():
    'Function that initiates all stateful stores to default values (and can be used to reset it)'
    # Global store for all received messages
    # ID: {"content: "messagetext", "channel":channel_id, "author": author_id, "reply": optional_reply_id, "attachments":[list of URLs]}. Ordered chronologically. possibly add timestamp too?
    global message_store
    message_store = {}

    # indexed by channel ID, each is a list of message IDs that are appended in chronological order
    global channel_order
    channel_order = {}

    # For each author, a list of their message IDs
    global messages_by_author
    messages_by_author = {} #each item is a list

    # store of known users. maps ID to string name
    global user_store
    user_store = {}

# TODO: maybe make message_store persist, but

def reset_templates():
    #dict of channel_id:template for each channel
    global templates
    templates = {}

reset() #run this during init
reset_templates()

# ATTENTION: ----- CONSTANTS AND ENVS -----

def load_env(filename=".env"):
    'Loads the values from the .env file into the global constants.'
    # Load configuration from file
    cd = {} # "config dict"
    with open(filename, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            cd[key.strip()] = val.strip()
    #dict now SHOULD contain strings of all values
    global bot_token
    bot_token = cd["DISCORD_BOT_TOKEN"]
    global admin_id
    admin_id = int(cd["ADMIN_ID"])
    global context_limit
    context_limit = int(cd["CONTEXT_LIMIT"]) #32 seems good?
    global command_prefix
    command_prefix = cd["COMMAND_PREFIX"]
    assert len(command_prefix) == 1 # command prefix gotta be one character
    global randomchannels
    randomchannels = eval(cd["RANDOMCHANNELS"]) # channels in which the bot may randomly respond to messages
    global autoresponse_words
    autoresponse_words = eval(cd["AUTORESPONSE"]) # words which will always trigger a bot response (if sent in randomchannels)
    global autoresponse_emoji
    autoresponse_emoji = eval(cd["AUTORESPONSE_EMOJI"]) #reacting with this emoji will make the bot reply to that message

load_env() #can be re-run later if changes are desired

# ATTENTION: ----- SOME DISCORD BULLSHIT WHICH NEEDS TO BE HERE AND NOT LATER -----

self_id = None #on_ready, becomes the int which is the bot's own ID

# Intents needed: message content and guild messages. is this even needed???
intents = discord.Intents.default()
intents.message_content = True
intents.guild_messages = True

# Use commands.Bot for easy command handling
bot = commands.Bot(command_prefix=command_prefix, intents=intents) # change to not use functions?


# ATTENTION: ----- HELPER FUNCTIONS -----

def relevant_messages(message_id, context_limit=context_limit, simple = True):
    'Takes the ID of a message and returns a list of message IDs which are relevant to that message, eg. represent a coherent reply stream. This is a very heuristicy algorithm, so... yeah this is a mess.'
    #for now: simply return the last context_limit messages in the channel
    if(simple):
        return channel_order[message_store[message_id]["channel"]][-context_limit:]
    # do the more fancy parsing method
    channel_id = message_store[message_id]["channel"]
    output = []
    current = message_id
    for i in range(context_limit):
        output.append(current)
        if("reply" in message_store[current]):
            if(message_store[current]["reply"] in message_store):
                current = message_store[current]["reply"] #follow chain
            else: #this can happen if someone replies to a bot message outside database
                break # we can't fetch any more context anyway
        else:
            # just follow the last messages?
            channelindex = channel_order[channel_id].index(current) #get the current message position in chat
            beforecurrent = channel_order[channel_id][:channelindex]
            if(len(beforecurrent) > context_limit - i):
                beforecurrent[:context_limit - i] #select only the number of messages needed to get context_limit
            output = beforecurrent + output[::-1] # needs to be reversed? TODO confirm?
            break

    return output

def chat_message_filter(text):
    'Filters a discord message before the LLM sees it.'
    # iterate over all user_store elements and replace. TODO: there are faster ways to do this!
    for userid in user_store:
        candidate = "<@" + str(userid) + ">"
        if(candidate in text):
            text = text.replace(candidate, "@" + user_store[userid])
    return text

def get_image_urls(text):
    'Returns a list of all image URLs present within the string. If none, returns None'
    output = []
    for piece in text.split(" "): #is this valid?
        if(piece.startswith("https://") and ("png" in piece or "jpg" in piece or "webp" in piece)):
            output.append(piece)
    return output

# def bot_message_filter(text):
#     'Filters text that comes from the bot so it doesnt have formatting problems'
#     # TODO: replace @name with actual pings as <@id>
#     # TODO: if the bot starts with its own name, delete that
#     if(text == ""):
#             return ""
#     #if it contains any usernames, replace with proper pings
#     try:
#         # Stop it from beginning lines with with "@user: " like its prompt says
#         acc = []
#         for line in text.split("\n"):
#             stripline = line.strip()
#             if(stripline == ""):
#                 continue
#             elif(":" in stripline): # TODO: find a better way of avoiding this
#                 acc.append(stripline.split(": ", 1)[1])
#             else:
#                 acc.append(stripline)
#         text = "\n".join(acc)
#         # if the bot is spamming newlines, delete them
#         if(text.count("\n") > 5):
#             text = text.replace("\n", "")
#         # fish
#         text = text.strip()
#         if(text == "fish"):
#             text = "<:pike1:852710661875564555><:pike2:852710683368095785><:pike3:852710702648393730><:pike4:852710722035384391>"
#         # TODO: should it uncensor words like "f**k" here?
#         return text
#     except:
#         print("bot_message_filter(", text, ") failed!")
#         return "<:pike1:852710661875564555><:pike2:852710683368095785><:pike3:852710702648393730><:pike4:852710722035384391>:interrobang:"

def bot_message_filter(text):
    'Filters text that comes from the bot so it doesnt have formatting problems'
    # TODO: replace @name with actual pings as <@id>
    if(text == ""):
        return ""
    # Stop it from beginning lines with with "@user: " like its prompt says
    acc = []
    for line in text.split("\n"):
        stripline = line.strip()
        if(stripline == ""):
            continue
        elif(":" in stripline): # TODO: find a better way of avoiding this
            acc.append(stripline.split(": ", 1)[1])
        else:
            acc.append(stripline)
    text = "\n".join(acc)
    # if the bot is spamming newlines, delete them
    if(text.count("\n") > 5):
        text = text.replace("\n", " ")
    # fish
    text = text.strip()
    # TODO: should it uncensor words like "f**k" here?
    if(len(text) > 2048):
        text = text[:2048] #crop if too long
    return text


def messages_to_llmsession(messages):
    'Takes a list of messages (either ID or just content strings), and creates an llmsession'
    # TODO: ignore images except for the bottom 3 (5?) messages
    channel_id = message_store[messages[0]]["channel"]
    chat = llmsession({"template": templates.get(channel_id, default_template)})
    for message_id in messages:
        if(isinstance(message_id, int)):
            if(message_store[message_id]["author"] == self_id): #if this is the bot itself
                role = "assistant"
            else:
                role = "user"
            content = message_store[message_id]["content"].strip() #get rid of blank space
            if(message_store[message_id]["attachments"] != []):
                images = message_store[message_id]["attachments"]
            else:
                images = []
            # images += get_image_urls(content) #add any URLs included in the text # BROKEN because discord seems to reject requests from llama-server. TODO?
            #add usernames
            content = '@' + user_store[message_store[message_id]["author"]] + ": " + content # maybe change : for :: so that the filter doesn't replace stray : ?
        else: #message_id is actually just a string
            role = "user" #just assume this
            content = message_id.strip() # can't add username?
            images = []
        #check if content is a URL, if it is, put it in the images
        if(content.startswith("https:") and (".jpg" in content or ".png" in content or ".webp" in content)):
            images.append(content)
            content = "\n"
        #add it to the object
        chat.addmessage(role, content, images)
    return chat #llmsession object!

def get_bot_message(session):
    'Uses the LLM to respond to an llmsession. Filters the response to be a reasonable discord message.'
    return bot_message_filter(session.getreply())

def get_bot_response(message_id):
    'This is the function that does the FULL response stack: get revelant messages, make session, respond, and filter. Returns a single string.'
    context = relevant_messages(message_id)
    session = messages_to_llmsession(context)
    # print(session) #DEBUG
    response = get_bot_message(session)
    return response

def get_llm_reaction(message_id):
    'Hopefully, returns an emoji that represents a reaction. Might just be jibberish.'
    text = message_store[message_id]["content"]
    chat = llmsession({"template": "Respond with a single emoji which you think is a funny and possibly insulting reaction to this message.", "max_tokens":16})
    chat.addmessage("user", text)
    return chat.getreply()[0]

# ATTENTION: ----- BOT EVENTS -----

@bot.event
async def on_ready():
    global self_id
    self_id = bot.user.id #VERY IMPORTANT!!!
    print(f"Logged in as {bot.user} ({bot.user.id})")


@bot.event
async def on_reaction_add(reaction, user):
    #if it's a robot reaction, reply to the message
    if(reaction.emoji == autoresponse_emoji and reaction.message.channel.id in randomchannels and reaction.message.id in message_store):
        response = get_bot_response(reaction.message.id)
        await reaction.message.reply(response)

@bot.event
async def on_message(message: discord.Message):
    #sometimes, on_message can be triggered before the bot has initialized properly. If so, ignore it.
    if(self_id == None):
        return
    #dereference frequently referenced stuff
    message_id = message.id
    author_id = message.author.id
    message_content = message.content
    channel_id = message.channel.id

    #file message info away in stores
    global message_store
    message_store[message_id] = {"content":message_content, "channel":message.channel.id, "author":author_id, "attachments":[]}
    if(message.reference != None): #technically this is also triggered by other stuff like pins and threads
        if(message.reference.message_id != None): #can be none under certain circumstances apparently, see https://discordpy.readthedocs.io/en/latest/api.html#discord.MessageReference.message_id
            message_store[message_id]["reply"] = message.reference.message_id

    if(message.attachments != None):
        for x in message.attachments:
            message_store[message_id]["attachments"].append(x.url)

    #add to message by author
    global messages_by_author
    if(author_id not in messages_by_author): # create entry in messages_by_author if it doesn't exist
        messages_by_author[author_id] = []
    messages_by_author[author_id].append(message_id) # add to messages_by_author

    global user_store
    if(author_id not in user_store): #if we haven't seen this user before, make an entry for them
        user_store[author_id] = message.author.display_name

    #channel order
    global channel_order
    if(channel_id not in channel_order):
        channel_order[channel_id] = []
    channel_order[channel_id].append(message_id) # message.id

    #if this is a self-message, ignore it. maybe undo this because it's funny?
    if(message.author.id == self_id): return

    #parse commands
    if(message_content.strip() != ""):
        if(message_content[0] == command_prefix):
            command_worked = False
            command = message_content[1:].split(" ")[0] #this assumes that command_prefix is one letter!
            if(command == "template"):
                global templates
                newtemplate = message_content[9:]
                if(newtemplate.strip() == ""): #reset if blank
                    newtemplate = default_template
                templates[message.channel.id] = newtemplate
                command_worked = True
            elif(command == "reset"):
                reset()
                command_worked = True
            elif(author_id == admin_id): #put priveleged commands in here!
                if(command == "run"):
                    try:
                        result = eval(message_content[4:], globals(), locals())
                    except Exception as e:
                        result = f"Error: {e}"
                    if(result != None):
                        await message.channel.send(str(result))
                    command_worked = True
            #maybe add a message for failed commands
            if(command_worked):
                await message.add_reaction("\u2705")
            else:
                await message.add_reaction("\u2754")
            return

    # Autoresponse detection
    is_mentioned = bot.user in message.mentions #I think this also returns true if replied with ping on?
    has_autoresponse = any(word in message.content.lower() for word in autoresponse_words)
    in_randomchannel = message.channel.id in randomchannels
    chance = random.randint(0,3) == 0

    if((random.randint(0,3) == 0) and in_randomchannel):
        #random react!
        random_reaction = get_llm_reaction(message_id)
        try:
            await message.add_reaction(random_reaction)
        except Exception as e:
            print("Reaction failed! ", str(e))
            pass #just ignore it lmfao

    # reply?
    if is_mentioned or (in_randomchannel and has_autoresponse) or (in_randomchannel and chance): # message.reference?
        if message.author.bot:
            #reduce bot spam conversations by replying to bots less
            if(random.randint(0,2) != 0):
                pass
                # time.sleep(5) # disabled because it blocks the entire process. Also causes a race condition where it can reply to the same message twice, breaking the user - assistant - user loop.
            else:
                return #exit early to avoid spam
        async with message.channel.typing():
            # llm_resp = await get_llm_response(message.content, message)
            llm_resp = get_bot_response(message_id)
            if not llm_resp.strip(): #if blank
                return
            if is_mentioned or random.randint(0,2) == 0: #randomly switch between direct replies and just saying it
                await message.reply(llm_resp)
            else:
                await message.channel.send(llm_resp)
            return


# ATTENTION: ----- LLM CLASS DEFINITION (can be changed for different endpoints?) -----

class llmsession:
    'A class which represents a typical LLM chat interface.'
    def __init__(self, params = {}):
        self.params = {"url":"http://localhost:8080/v1/", "model":"localllm", "temperature":0.9, "max_tokens":512, "stream":False, "template":None, "thinking":False} | params #default parameters here
        self.history = [] #List of dicts. Each dict has "role", "content", "assets" (A URL list, optionally empty), and "thinking" (a probably blank string).
        if(self.params["template"] != None):
            self.addmessage("system", self.params["template"]) #should this be added at the *end*, where the LLM can see it most immediately?
    def __str__(self):
        'Makes the LLM history into a clean block of terminal-readable text.'
        output = ""
        for message in self.history:
            output += message["role"] + "\n    " + message["content"] + "\n\n"
        return output
    def lastmessage(self):
        'Reads the content of the last message.'
        return self.history[-1]["content"]
    def addmessage(self, role, content, assets=[], thinking="", merge=True):
        'Appends another message with a role and some content. Images is a list of URLs. If merge is enabled, it will merge neighboring messages into a simple back and forth stream, needed for some models.'
        if(merge == True and len(self.history) > 1):
            if(self.history[-1]["role"] == role):
                self.history[-1]["content"] += "\n\n" + content
                # should I merge thinking? I don't know how that'd work
                self.history[-1]["assets"] += assets
                return
        self.history.append({"role":role, "content":content, "assets":assets, "thinking":thinking}) #create a new message
    def getjson(self):
        "Gets the JSON for a request to an LLM server. Returns as a Python-style dict."
        output = {"messages":[]}
        #Pass through many of the params into the JSON header
        for i in ("model", "temperature", "max_tokens", ): #THESE ARE THE PARAMETER NAMES TO PASS THROUGH TO THE JSON!
            output[i] = self.params[i]
        #set chat_template_kwargs
        output["chat_template_kwargs"] = {"enable_thinking":self.params["thinking"],}
        #add entries for messages
        for message in self.history:
            output["messages"].append({"role":message["role"], "content":[{"type": "text","text":message["content"]}]}) #TODO; does thinking get prepended here or does it go in a different category?
            if(message["assets"] != []):
                for x in message["assets"]:
                    # print(repr(output))
                    output["messages"][-1]["content"].append({"type": "image_url","image_url":{"url":x}})
                    # see: the structuring is stupid https://llama.app/docs/api
        return output

    def getreply(self, retries=1):
        'Uses the LLM to respond to the previous history. Returns a string, does NOT modify the history.'
        #Get response json
        json_section = self.getjson()

        resp = requests.post(self.params["url"] + "chat/completions",
                        headers = {"Content-Type": "application/json"},
                        json = json_section,
                        stream=self.params["stream"])
        #result
        r = resp.json()
        # WARNING: bug somewhere here where the result doesn't have "choices" sometimes?
        try:
            return r["choices"][0]["message"]["content"]
        except:
            print("ERROR:", str(resp))
        if(retries > 0):
            return self.getreply(retries=retries - 1) #recurse until failure
        else:
            raise Exception("llmsession failed to get a reply: " + str(resp) + "!")

    def addreply(self):
        'Use getreply to add a new message to the history.'
        self.addreply("assistant", self.getreply())


# ATTENTION: ----- ACTUAL STARTUP (except for the prior discord bullshit) -----

if __name__ == "__main__":
    bot.run(bot_token)
