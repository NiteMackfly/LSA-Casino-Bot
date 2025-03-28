import io
import os
import random
import asyncio
from typing import List, Tuple

import discord
from discord.ext import commands
from discord.ui import View, Button
from modules.card import Card
from modules.economy import Economy
from modules.helpers import *
from PIL import Image
from modules.exceptions import ActiveGameError  # Adjust the import path as necessary


class BlackjackView(View):
    def __init__(self, game, user_id):
        super().__init__()
        self.game = game
        self.user_id = user_id
        self.value = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Ensure only the user who started the game can interact."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "You are not allowed to use this button.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary, emoji="🇭")
    async def hit(self, interaction: discord.Interaction, button: Button):
        self.value = "hit"
        self.stop()

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary, emoji="🇸")
    async def stand(self, interaction: discord.Interaction, button: Button):
        self.value = "stand"
        self.stop()


class Blackjack(commands.Cog):
    def __init__(self, client: commands.Bot):
        self.client = client
        self.economy = Economy()
        self.active_players = set()

    def cog_check(self, ctx):
        if ctx.author.id in self.active_players:
            raise ActiveGameError("You have an ongoing game. Please finish it first.")
        return True

    def check_bet(
        self,
        ctx: commands.Context,
        bet: int = DEFAULT_BET,
    ):
        bet = int(bet)
        if bet <= 0:
            raise commands.errors.BadArgument()
        current = self.economy.get_entry(ctx.author.id)[1]
        if bet > current:
            raise InsufficientFundsException(current, bet)

    @staticmethod
    def hand_to_images(hand: List[Card]) -> List[Image.Image]:
        return [
            Image.open(os.path.join(ABS_PATH, "modules/cards/", card.image))
            for card in hand
        ]

    @staticmethod
    def center(*hands: Tuple[Image.Image]) -> Image.Image:
        """Creates blackjack table with cards placed"""
        bg: Image.Image = Image.open(os.path.join(ABS_PATH, "modules/", "table.png"))
        bg_center_x = bg.size[0] // 2
        bg_center_y = bg.size[1] // 2

        img_w = hands[0][0].size[0]
        img_h = hands[0][0].size[1]

        start_y = bg_center_y - (((len(hands) * img_h) + ((len(hands) - 1) * 15)) // 2)
        for hand in hands:
            start_x = bg_center_x - (
                ((len(hand) * img_w) + ((len(hand) - 1) * 10)) // 2
            )
            for card in hand:
                bg.alpha_composite(card, (start_x, start_y))
                start_x += img_w + 10
            start_y += img_h + 15
        return bg

    @staticmethod
    def calc_hand(hand: List[List[Card]]) -> int:
        """Calculates the sum of the card values and accounts for aces"""
        non_aces = [c for c in hand if c.symbol != "A"]
        aces = [c for c in hand if c.symbol == "A"]
        sum = 0
        for card in non_aces:
            if not card.down:
                if card.symbol in "JQK":
                    sum += 10
                else:
                    sum += card.value
        for card in aces:
            if not card.down:
                if sum <= 10:
                    sum += 11
                else:
                    sum += 1
        return sum

    def output(self, *hands: Tuple[List[Card]]) -> io.BytesIO:
        image = self.center(*map(self.hand_to_images, hands))
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format="PNG")
        img_byte_arr.seek(0)
        return img_byte_arr

    @commands.command(
        aliases=["bj"],
        brief="Play a simple game of blackjack.\nBet must be greater than $0",
        usage=f"blackjack [bet- default=${DEFAULT_BET}]",
    )
    async def blackjack(self, ctx: commands.Context, bet: int = DEFAULT_BET):
        if ctx.author.id in self.active_players:
            await ctx.send(
                "You have an ongoing game. Please finish it first.", ephemeral=True
            )
            return

        self.active_players.add(ctx.author.id)

        try:
            self.check_bet(ctx, bet)
            deck = [Card(suit, num) for num in range(2, 15) for suit in Card.suits]
            random.shuffle(deck)  # Generate deck and shuffle it

            player_hand: List[Card] = []
            dealer_hand: List[Card] = []

            player_hand.append(deck.pop())
            dealer_hand.append(deck.pop())
            player_hand.append(deck.pop())
            dealer_hand.append(deck.pop().flip())

            player_score = self.calc_hand(player_hand)
            dealer_score = self.calc_hand(dealer_hand)

            async def out_table(**kwargs) -> Tuple[discord.Embed, discord.File]:
                """Creates an embed and file for the current table"""
                img_byte_arr = self.output(dealer_hand, player_hand)
                embed = make_embed(**kwargs)
                file = discord.File(fp=img_byte_arr, filename="blackjack.png")
                embed.set_image(url="attachment://blackjack.png")
                return embed, file

            standing = False
            msg = None

            while True:
                player_score = self.calc_hand(player_hand)
                dealer_score = self.calc_hand(dealer_hand)
                if player_score == 21:  # win condition
                    bet = int(bet * 1.5)
                    self.economy.add_money(ctx.author.id, bet)
                    result = ("Blackjack!", "won")
                    break
                elif player_score > 21:  # losing condition
                    self.economy.add_money(ctx.author.id, bet * -1)
                    result = ("Player busts", "lost")
                    break

                embed, file = await out_table(
                    title="Your Turn",
                    description=f"Your hand: {player_score}\n"
                    f"Dealer's hand: {dealer_score}",
                )

                view = BlackjackView(self, ctx.author.id)
                if msg:
                    await msg.edit(embed=embed, attachments=[file], view=view)
                    del file
                else:
                    msg = await ctx.reply(file=file, embed=embed, view=view)
                    del file

                try:
                    await asyncio.wait_for(
                        view.wait(), timeout=90.0
                    )  # Timeout after 10 seconds
                except asyncio.TimeoutError:
                    view.value = "stand"  # Automatically stand after timeout

                if view.value == "hit":
                    player_hand.append(deck.pop())
                    continue
                elif view.value == "stand":
                    standing = True
                    break

            if standing:
                dealer_hand[1].flip()
                player_score = self.calc_hand(player_hand)
                dealer_score = self.calc_hand(dealer_hand)

                while dealer_score < 17:  # dealer draws until 17 or greater
                    dealer_hand.append(deck.pop())
                    dealer_score = self.calc_hand(dealer_hand)

                if dealer_score == 21:  # winning/losing conditions
                    self.economy.add_money(ctx.author.id, bet * -1)
                    result = ("Dealer blackjack", "lost")
                elif dealer_score > 21:
                    self.economy.add_money(ctx.author.id, bet)
                    result = ("Dealer busts", "won")
                elif dealer_score == player_score:
                    result = ("Tie!", "kept")
                elif dealer_score > player_score:
                    self.economy.add_money(ctx.author.id, bet * -1)
                    result = ("You lose!", "lost")
                elif dealer_score < player_score:
                    self.economy.add_money(ctx.author.id, bet)
                    result = ("You win!", "won")

            color = (
                discord.Color.red()
                if result[1] == "lost"
                else (
                    discord.Color.green()
                    if result[1] == "won"
                    else discord.Color.blue()
                )
            )

            embed, file = await out_table(
                title=result[0],
                color=color,
                description=(
                    f"**You {result[1]} ${bet}**\nYour hand: {player_score}\n"
                    + f"Dealer's hand: {dealer_score}"
                ),
            )
            if msg:
                await msg.edit(embed=embed, attachments=[file], view=None)
                del file
            else:
                await ctx.reply(file=file, embed=embed)
                del file

        finally:
            self.active_players.remove(ctx.author.id)


async def setup(client: commands.Bot):
    await client.add_cog(Blackjack(client))
