import asyncio
import logging
import os
import re
import threading

import discord
from discord.ext import commands
from dotenv import load_dotenv
from flask import Flask

# =========================================================
# KONFIGURACJA
# =========================================================

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

WELCOME_CHANNEL_ID = 1567940953040232551
AUTO_ROLE_ID = 1568694462601887956
TICKET_PANEL_CHANNEL_ID = 1568699441488076832

# !!! WPISZ PRAWDZIWE ID ROLI ZARZĄDU !!!
STAFF_ROLE_ID = 123456789012345678

# =========================================================
# OBRAZKI
# =========================================================

WELCOME_IMAGE_URL = (
    "https://cdn.discordapp.com/attachments/"
    "1569024383845007511/1569436242750472233/"
    "a42800a3-c618-430e-808d-423899e55d90.png"
    "?ex=6af372e6&is=6af22166"
    "&hm=a78829c7f54cda65d5183b2264be55e8832b78b72b59fec505a40dd3dd416c05&"
)

TICKET_IMAGE_URL = (
    "https://cdn.discordapp.com/attachments/"
    "1569024383845007511/1569436255588974652/"
    "3ca61350-1370-469d-b000-c9faa24424b0.png"
    "?ex=6af372ea&is=6af2216a"
    "&hm=46722fec18d4668e84ae22cc4e3db9808a6f056804bbe9661ade08a4e619654b&"
)

# Czysty rgb(0,0,0) Discord traktuje jak "brak koloru",
# więc używamy prawie-czerni.
COLOR = discord.Colour(0x010101)

PORT = int(os.getenv("PORT", "8080"))

# =========================================================
# EMOJI
# =========================================================

EMOJI_MEMBER = "<:czlonek:1567976307302735902>"
EMOJI_SUPER = "<:super:1567984387457421473>"
EMOJI_RULES = "<:regulamin:1567980863826690159>"
EMOJI_TICKET = "<:ticket:1567981310788509786>"
EMOJI_WELCOME = "<:witaj:1567978044956737587>"

EMOJI_RECRUITMENT = "<a:rekrutacja:1568746446528716920>"
EMOJI_ALLIANCE = "<:sojusz:1568745977316114545>"
EMOJI_QUESTION = "<:pytanie:1568742288249816530>"
EMOJI_WARNING = "<:warning:1568745877059670086>"

EMOJI_PERSON = "<:osoba_:1569027160235319306>"
EMOJI_HASHTAG = "<:hasztag_:1569027212085043330>"
EMOJI_PENCIL = "<:Dlugopis_:1569027448945905744>"

# =========================================================
# KATEGORIE TICKETÓW
# =========================================================

TICKET_TYPES = {
    "rekrutacja": {
        "label": "Rekrutacja",
        "title": "REKRUTACJA",
        "description": "Dołącz do gildii HUNT.",
        "emoji": EMOJI_RECRUITMENT,
        "fallback_emoji": "📝",
        "category_id": 1568749095517036705,
    },
    "sojusz": {
        "label": "Sojusz",
        "title": "SOJUSZ",
        "description": "Zaproponuj współpracę lub sojusz.",
        "emoji": EMOJI_ALLIANCE,
        "fallback_emoji": "🤝",
        "category_id": 1568749019197608057,
    },
    "pytanie": {
        "label": "Pomoc / Pytanie",
        "title": "POMOC / PYTANIE",
        "description": "Masz pytanie lub potrzebujesz pomocy?",
        "emoji": EMOJI_QUESTION,
        "fallback_emoji": "❓",
        "category_id": 1568748991749816530,
    },
}

# =========================================================
# FLASK - KEEP ALIVE
# =========================================================

app = Flask(__name__)
logging.getLogger("werkzeug").setLevel(logging.ERROR)


@app.route("/")
def home():
    return "HUNT BOT ONLINE!"


def run_flask():
    app.run(host="0.0.0.0", port=PORT, use_reloader=False)


# =========================================================
# BOT
# =========================================================

intents = discord.Intents.default()
intents.members = True
intents.message_content = True


class HUNTBot(commands.Bot):

    async def setup_hook(self):
        # Widoki persistent - działają po restarcie bota
        self.add_view(TicketPanel())
        self.add_view(CloseTicketView())


bot = HUNTBot(command_prefix="!", intents=intents)
panel_sent = False

# =========================================================
# FUNKCJE POMOCNICZE
# =========================================================


def clip(text: str, limit: int) -> str:
    """Przycina tekst do limitu Discorda."""
    text = (text or "").strip()

    if not text:
        return "Nie podano"

    return text if len(text) <= limit else text[: limit - 1] + "…"


def resolve_emoji(raw: str, fallback: str) -> str:
    """
    Zwraca emoji serwerowe tylko jeśli bot je widzi.
    W przeciwnym razie używa zwykłego emoji.
    """

    match = re.fullmatch(r"<a?:\w+:(\d+)>", raw or "")

    if not match:
        return raw or fallback

    if not bot.is_ready():
        return fallback

    if bot.get_emoji(int(match.group(1))) is None:
        print(
            f"Uwaga: emoji {raw} nie istnieje lub bot go nie widzi "
            f"- używam zastępczego."
        )
        return fallback

    return raw


def owner_topic(user_id: int) -> str:
    return f"ticket-owner:{user_id}"


def find_open_ticket(guild: discord.Guild, user_id: int):
    """Zwraca otwarty ticket użytkownika lub None."""

    topic = owner_topic(user_id)

    for channel in guild.text_channels:
        if channel.topic == topic:
            return channel

    return None


def is_staff(member: discord.Member, guild: discord.Guild) -> bool:

    staff_role = guild.get_role(STAFF_ROLE_ID)

    if staff_role and staff_role in member.roles:
        return True

    return member.guild_permissions.administrator


# =========================================================
# POWITANIE
# =========================================================


def create_welcome_embed(member: discord.Member) -> discord.Embed:

    embed = discord.Embed(
        description=(
            f"**Witaj {member.mention} na Serwerze Gildia HUNT**\n\n"
            f"Cieszymy się, że z nami jesteś, mamy nadzieję że zostaniesz "
            f"z nami na dłużej!\n"
            f"Pamiętaj, aby zapoznać się z regulaminem serwera.\n\n"
            f"Jesteś naszym **{member.guild.member_count}** użytkownikiem!"
        ),
        color=COLOR,
    )

    embed.set_image(url=WELCOME_IMAGE_URL)

    return embed


@bot.event
async def on_member_join(member: discord.Member):

    # Automatyczna rola
    role = member.guild.get_role(AUTO_ROLE_ID)

    if role:
        try:
            await member.add_roles(
                role,
                reason="Automatyczna rola"
            )

        except discord.Forbidden:
            print(
                "Bot nie ma uprawnień do nadania roli "
                "(sprawdź hierarchię ról)."
            )

        except discord.HTTPException as error:
            print(f"Błąd nadawania roli: {error}")

    # Kanał powitań
    channel = member.guild.get_channel(WELCOME_CHANNEL_ID)

    if channel:
        try:
            await channel.send(
                content=member.mention,
                embed=create_welcome_embed(member)
            )

        except discord.HTTPException as error:
            print(f"Błąd wysyłania powitania: {error}")


@bot.command(name="testpowitanie")
@commands.has_permissions(administrator=True)
async def test_powitanie(ctx: commands.Context):

    await ctx.send(
        content=ctx.author.mention,
        embed=create_welcome_embed(ctx.author)
    )


@bot.event
async def on_command_error(ctx: commands.Context, error):

    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.MissingPermissions):
        await ctx.send(
            f"{EMOJI_WARNING} Nie masz uprawnień do tej komendy."
        )
        return

    print(f"Błąd komendy: {error}")


# =========================================================
# EMBED TICKETA
# =========================================================


def create_ticket_embed(
    user: discord.Member,
    ticket_channel: discord.TextChannel,
    ticket_type: str,
    answers: dict,
) -> discord.Embed:

    title = TICKET_TYPES[ticket_type]["title"]

    embed = discord.Embed(
        title=f"TICKET • {title}",
        description=(
            f"{EMOJI_PERSON} **Zgłaszający:** {user.mention}\n"
            f"{EMOJI_HASHTAG} **Kanał:** {ticket_channel.mention}\n"
            f"{EMOJI_PENCIL} **Kategoria:** {title}"
        ),
        color=COLOR,
        timestamp=discord.utils.utcnow(),
    )

    # Każda odpowiedź w osobnym polu
    for question, answer in answers.items():

        embed.add_field(
            name=f"▸ {question}",
            value=f">>> {clip(answer, 1000)}",
            inline=False,
        )

    embed.set_author(
        name="GILDIA HUNT • SYSTEM TICKET",
        icon_url=(
            user.guild.icon.url
            if user.guild.icon
            else None
        ),
    )

    embed.set_thumbnail(url=user.display_avatar.url)

    embed.set_footer(
        text="HUNT • SYSTEM ZGŁOSZEŃ"
    )

    return embed


# =========================================================
# TWORZENIE TICKETÓW
# =========================================================


async def create_ticket(
    interaction: discord.Interaction,
    ticket_type: str,
    answers: dict,
):

    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild

    if guild is None:
        await interaction.followup.send(
            f"{EMOJI_WARNING} Nie udało się znaleźć serwera.",
            ephemeral=True,
        )
        return

    # Zabezpieczenie przed dublami
    existing = find_open_ticket(
        guild,
        interaction.user.id
    )

    if existing:
        await interaction.followup.send(
            f"{EMOJI_WARNING} Masz już otwarty ticket: "
            f"{existing.mention}",
            ephemeral=True,
        )
        return

    category = guild.get_channel(
        TICKET_TYPES[ticket_type]["category_id"]
    )

    if not isinstance(
        category,
        discord.CategoryChannel
    ):
        await interaction.followup.send(
            f"{EMOJI_WARNING} Nie znaleziono kategorii na serwerze. "
            f"Sprawdź ID kategorii.",
            ephemeral=True,
        )
        return

    # =====================================================
    # UPRAWNIENIA
    # =====================================================

    overwrites = {
        guild.default_role:
            discord.PermissionOverwrite(
                view_channel=False
            ),

        interaction.user:
            discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
    }

    staff_role = guild.get_role(
        STAFF_ROLE_ID
    )

    if staff_role:
        overwrites[staff_role] = (
            discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            )
        )

    if guild.me:
        overwrites[guild.me] = (
            discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_channels=True,
                read_message_history=True,
                embed_links=True,
            )
        )

    # =====================================================
    # NAZWA KANAŁU
    # =====================================================

    username = re.sub(
        r"[^a-z0-9-]",
        "",
        interaction.user.name.lower().replace(" ", "-")
    )

    if not username:
        username = str(
            interaction.user.id
        )[-4:]

    channel_name = (
        f"{ticket_type}-{username}"
    )[:90]

    # =====================================================
    # TWORZENIE KANAŁU
    # =====================================================

    try:

        ticket_channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            topic=owner_topic(
                interaction.user.id
            ),
            reason=(
                f"Ticket użytkownika "
                f"{interaction.user}"
            ),
        )

        embed = create_ticket_embed(
            interaction.user,
            ticket_channel,
            ticket_type,
            answers,
        )

        await ticket_channel.send(
            content=interaction.user.mention,
            embed=embed,
            view=CloseTicketView(),
        )

        await interaction.followup.send(
            f"{EMOJI_TICKET} Utworzono ticket: "
            f"{ticket_channel.mention}",
            ephemeral=True,
        )

    except discord.Forbidden:

        await interaction.followup.send(
            f"{EMOJI_WARNING} Bot nie ma uprawnień "
            f"do tworzenia kanałów ticketów.",
            ephemeral=True,
        )

    except discord.HTTPException as error:

        print(
            f"Błąd tworzenia ticketa: {error}"
        )

        await interaction.followup.send(
            f"{EMOJI_WARNING} Wystąpił błąd "
            f"podczas tworzenia ticketa.",
            ephemeral=True,
        )


# =========================================================
# FORMULARZE
# =========================================================


class RecruitmentModal(
    discord.ui.Modal,
    title="REKRUTACJA HUNT"
):

    dlaczego = discord.ui.TextInput(
        label="Dlaczego chcesz dołączyć do HUNT?",
        placeholder="Napisz, dlaczego chcesz dołączyć...",
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )

    pvp = discord.ui.TextInput(
        label="Jak oceniasz swoje umiejętności PVP?",
        placeholder="Opisz swoje umiejętności...",
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )

    wiek_premium = discord.ui.TextInput(
        label="Ile masz lat i czy posiadasz premium?",
        placeholder="Wiek oraz informacja o premium...",
        max_length=200,
    )

    main = discord.ui.TextInput(
        label="Jaki jest Twój główny tryb?",
        placeholder="Podaj swój główny tryb...",
        max_length=200,
    )

    gildie = discord.ui.TextInput(
        label="W jakich gildiach wcześniej grałeś?",
        placeholder="Wymień poprzednie gildie...",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1000,
    )

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        answers = {
            "Dlaczego chcesz dołączyć do HUNT?":
                self.dlaczego.value,

            "Umiejętności PVP":
                self.pvp.value,

            "Wiek i konto premium":
                self.wiek_premium.value,

            "Główny tryb":
                self.main.value,

            "Poprzednie gildie":
                self.gildie.value,
        }

        await create_ticket(
            interaction,
            "rekrutacja",
            answers
        )


class AllianceModal(
    discord.ui.Modal,
    title="SOJUSZ HUNT"
):

    tag = discord.ui.TextInput(
        label="Jaki jest TAG Twojej gildii?",
        placeholder="Podaj TAG gildii...",
        max_length=100,
    )

    czlonkowie = discord.ui.TextInput(
        label="Ilu członków liczy Twoja gildia?",
        placeholder="Podaj liczbę członków...",
        max_length=100,
    )

    lider = discord.ui.TextInput(
        label="Kto jest liderem gildii?",
        placeholder="Podaj nick lidera...",
        max_length=100,
    )

    serwery = discord.ui.TextInput(
        label="Na jakich serwerach gracie?",
        placeholder="Podaj serwery...",
        style=discord.TextStyle.paragraph,
        max_length=500,
    )

    pvp = discord.ui.TextInput(
        label="Czy gildia bierze udział w PVP?",
        placeholder="Napisz coś o aktywności PVP...",
        style=discord.TextStyle.paragraph,
        max_length=500,
    )

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        answers = {
            "TAG gildii":
                self.tag.value,

            "Liczba członków":
                self.czlonkowie.value,

            "Lider gildii":
                self.lider.value,

            "Serwery":
                self.serwery.value,

            "Aktywność PVP":
                self.pvp.value,
        }

        await create_ticket(
            interaction,
            "sojusz",
            answers
        )


class QuestionModal(
    discord.ui.Modal,
    title="POMOC / PYTANIE"
):

    pytanie = discord.ui.TextInput(
        label="W czym potrzebujesz pomocy?",
        placeholder="Opisz swoje pytanie...",
        style=discord.TextStyle.paragraph,
        max_length=1500,
    )

    problem = discord.ui.TextInput(
        label="Dodatkowe informacje",
        placeholder="Możesz podać dodatkowe informacje...",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1000,
    )

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        answers = {
            "W czym potrzebujesz pomocy?":
                self.pytanie.value,

            "Dodatkowe informacje":
                self.problem.value,
        }

        await create_ticket(
            interaction,
            "pytanie",
            answers
        )


MODALS = {
    "rekrutacja": RecruitmentModal,
    "sojusz": AllianceModal,
    "pytanie": QuestionModal,
}

# =========================================================
# MENU WYBORU TICKETA
# =========================================================


class TicketPanelSelect(
    discord.ui.Select
):

    def __init__(self):

        options = [
            discord.SelectOption(
                label=data["label"],
                description=data["description"],
                value=key,
                emoji=resolve_emoji(
                    data["emoji"],
                    data["fallback_emoji"]
                ),
            )

            for key, data
            in TICKET_TYPES.items()
        ]

        super().__init__(
            placeholder="Wybierz kategorię ticketa...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="hunt_ticket_category_select",
        )

    async def callback(
        self,
        interaction: discord.Interaction
    ):

        guild = interaction.guild

        # Jeśli użytkownik ma już ticket
        if guild:

            existing = find_open_ticket(
                guild,
                interaction.user.id
            )

            if existing:

                await interaction.response.send_message(
                    f"{EMOJI_WARNING} Masz już otwarty ticket: "
                    f"{existing.mention}",
                    ephemeral=True,
                )

                await self._reset_panel(
                    interaction
                )

                return

        modal_class = MODALS.get(
            self.values[0]
        )

        if modal_class is None:

            await interaction.response.send_message(
                f"{EMOJI_WARNING} Nieznana kategoria.",
                ephemeral=True,
            )

            return

        await interaction.response.send_modal(
            modal_class()
        )

        await self._reset_panel(
            interaction
        )

    @staticmethod
    async def _reset_panel(
        interaction: discord.Interaction
    ):

        """
        Czyści zaznaczenie w menu,
        żeby można było wybrać tę samą opcję ponownie.
        """

        if interaction.message is None:
            return

        try:

            await interaction.message.edit(
                view=TicketPanel()
            )

        except discord.HTTPException:
            pass


# =========================================================
# PANEL TICKETÓW - COMPONENTS V2
# =========================================================


class TicketPanel(
    discord.ui.LayoutView
):

    def __init__(self):

        super().__init__(
            timeout=None
        )

        container = discord.ui.Container(

            # =================================================
            # NAGŁÓWEK
            # =================================================

            discord.ui.TextDisplay(
                "```🎫 GILDIA HUNT × TICKETY```"
            ),

            discord.ui.Separator(
                visible=True,
                spacing=discord.SeparatorSpacing.small
            ),

            # =================================================
            # OPIS
            # =================================================

            discord.ui.TextDisplay(
                f"{EMOJI_PENCIL}︲Potrzebujesz **pomocy**? "
                f"A może chcesz odebrać nagrodę? "
                f"**Wybierz** opcję z listy poniżej, "
                f"aby **stworzyć** ticket"
            ),

            discord.ui.Separator(
                visible=True,
                spacing=discord.SeparatorSpacing.small
            ),

            # =================================================
            # SELECT MENU
            # =================================================

            discord.ui.ActionRow(
                TicketPanelSelect()
            ),

            discord.ui.Separator(
                visible=True,
                spacing=discord.SeparatorSpacing.small
            ),

            # =================================================
            # OBRAZEK
            # =================================================

            discord.ui.MediaGallery(
                discord.MediaGalleryItem(
                    TICKET_IMAGE_URL
                )
            ),

            accent_colour=COLOR,
        )

        self.add_item(container)


# =========================================================
# ZAMYKANIE TICKETÓW
# =========================================================


class CloseTicketView(
    discord.ui.View
):

    def __init__(self):

        super().__init__(
            timeout=None
        )

    @discord.ui.button(
        label="Zamknij ticket",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="hunt_close_ticket",
    )
    async def close_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        channel = interaction.channel
        guild = interaction.guild

        if (
            not isinstance(
                channel,
                discord.TextChannel
            )
            or guild is None
        ):

            await interaction.response.send_message(
                f"{EMOJI_WARNING} Nie można zamknąć tego kanału.",
                ephemeral=True,
            )

            return

        owner = (
            channel.topic
            == owner_topic(
                interaction.user.id
            )
        )

        if (
            not owner
            and not is_staff(
                interaction.user,
                guild
            )
        ):

            await interaction.response.send_message(
                f"{EMOJI_WARNING} Nie masz uprawnień "
                f"do zamknięcia tego ticketa.",
                ephemeral=True,
            )

            return

        embed = discord.Embed(
            description=(
                f"🔒 Ticket zostanie zamknięty za **5 sekund**.\n"
                f"Zamyka: {interaction.user.mention}"
            ),
            color=COLOR,
        )

        await interaction.response.send_message(
            embed=embed
        )

        await asyncio.sleep(5)

        try:

            await channel.delete(
                reason=(
                    f"Ticket zamknięty przez "
                    f"{interaction.user}"
                )
            )

        except discord.NotFound:
            pass

        except discord.Forbidden:
            print(
                "Bot nie ma uprawnień "
                "do usunięcia kanału."
            )

        except discord.HTTPException as error:
            print(
                f"Błąd usuwania ticketa: {error}"
            )


# =========================================================
# WYSYŁANIE PANELU
# =========================================================


async def send_ticket_panel():

    try:

        channel = await bot.fetch_channel(
            TICKET_PANEL_CHANNEL_ID
        )

    except discord.NotFound:

        print(
            "Nie znaleziono kanału panelu ticketów."
        )

        return

    except discord.Forbidden:

        print(
            "Bot nie ma dostępu do kanału panelu."
        )

        return

    if not isinstance(
        channel,
        discord.TextChannel
    ):

        print(
            "Podane ID nie jest kanałem tekstowym."
        )

        return

    try:

        # Usuwamy stare panele bota,
        # żeby po restarcie nie było duplikatów.

        async for message in channel.history(
            limit=25
        ):

            if (
                message.author.id
                == bot.user.id
            ):

                try:

                    await message.delete()

                except discord.HTTPException:
                    pass

        await channel.send(
            view=TicketPanel()
        )

        print(
            "Wysłano panel ticketów HUNT."
        )

    except discord.Forbidden:

        print(
            "Bot nie ma uprawnień do "
            "wysyłania/usuwania wiadomości "
            "w kanale panelu."
        )

    except discord.HTTPException as error:

        print(
            f"Błąd wysyłania panelu: {error}"
        )


# =========================================================
# ON READY
# =========================================================


@bot.event
async def on_ready():

    global panel_sent

    print("--------------------------------")
    print(
        f"Zalogowano jako: {bot.user}"
    )
    print(
        f"ID bota: {bot.user.id}"
    )
    print("--------------------------------")

    if not panel_sent:

        panel_sent = True

        await send_ticket_panel()


# =========================================================
# URUCHOMIENIE
# =========================================================


if __name__ == "__main__":

    if not TOKEN:
        raise ValueError(
            "Brak tokena! "
            "Dodaj DISCORD_TOKEN do pliku .env"
        )

    threading.Thread(
        target=run_flask,
        daemon=True
    ).start()

    bot.run(TOKEN)
