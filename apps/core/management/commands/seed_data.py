"""
Populate the database with realistic demo data for Mbarara Auction.

    python manage.py seed_data
    python manage.py seed_data --password "MyDemoPass1!"

Everything is created through the same service layer the app uses
(apps.auctions.services, apps.bidding.services, apps.payments.services),
so statuses, audit logs, notifications and payments are consistent with
what real usage would produce.

Idempotent: users, categories and auctions are looked up by username /
name / title and skipped if they already exist, so it is safe to re-run.
Refuses to run when DEBUG is False unless --force is given, so demo
accounts with a known password can't land in production by accident.
"""
import logging
import os
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services import suspend_bidder, verify_profile
from apps.auctions import services as auction_services
from apps.auctions.models import Auction, AuctionCategory
from apps.bidding import services as bidding_services
from apps.bidding.models import Bid
from apps.payments import services as payment_services

D = Decimal

CATEGORIES = [
    "Electronics",
    "Vehicles",
    "Furniture",
    "Farm Equipment",
    "Livestock",
    "Land & Property",
    "Building Materials",
    "Home & Appliances",
]

# username, first, last, phone, city, verified?
SELLERS = [
    ("seller_agnes", "Agnes", "Kyomuhendo", "+256772100101", "Mbarara", True),
    ("seller_david", "David", "Byaruhanga", "+256772100102", "Mbarara", True),
    ("seller_rose", "Rose", "Nankunda", "+256772100103", "Ntungamo", True),
    ("seller_pending", "Peter", "Mugisha", "+256772100104", "Mbarara", False),
]

# username, first, last, phone, location, state: verified | pending | suspended
BIDDERS = [
    ("bidder_john", "John", "Ssemwogerere", "+256782200201", "Mbarara", "verified"),
    ("bidder_sarah", "Sarah", "Namubiru", "+256782200202", "Kabale", "verified"),
    ("bidder_isaac", "Isaac", "Mwesigwa", "+256782200203", "Bushenyi", "verified"),
    ("bidder_mary", "Mary", "Tukahirwa", "+256782200204", "Mbarara", "verified"),
    ("bidder_emma", "Emmanuel", "Orishaba", "+256782200205", "Ibanda", "verified"),
    ("bidder_pending", "Brenda", "Akampurira", "+256782200206", "Mbarara", "pending"),
    ("bidder_suspended", "Kevin", "Rwabwogo", "+256782200207", "Mbarara", "suspended"),
]

# Each auction: how it should end up. `bids` are (bidder, amount) in order.
# state: LIVE | SCHEDULED | SUBMITTED | DRAFT | REJECTED | CANCELLED
#        | CLOSED_WON (payment pending) | CLOSED_NO_WINNER | COMPLETED (paid)
AUCTIONS = [
    # ---- Live ---------------------------------------------------------
    dict(
        title="Toyota Hilux Double Cab 2016 (Diesel, 4x4)",
        category="Vehicles", seller="seller_agnes", state="LIVE",
        description="Well-maintained Hilux, 118,000 km, full service history, new tyres. "
                    "Single owner, logbook ready. Viewing at Kakoba by appointment.",
        location="Kakoba, Mbarara", start=D("68000000"), reserve=D("75000000"), inc=D("500000"),
        ends_in=timedelta(days=3, hours=4),
        bids=[("bidder_john", "69000000"), ("bidder_sarah", "70000000"),
              ("bidder_isaac", "71500000"), ("bidder_john", "73000000"), ("bidder_emma", "74000000")],
        invalidate_bid=(3, "Bid placed in error by bidder; confirmed by phone."),
    ),
    dict(
        title="Ankole Long-Horn Bull (4 years)",
        category="Livestock", seller="seller_david", state="LIVE",
        description="Healthy, vaccinated Ankole bull with a strong breeding record. "
                    "Vet certificate available. Collection from Rwampara.",
        location="Rwampara, Mbarara", start=D("2500000"), reserve=D("3000000"), inc=D("100000"),
        ends_in=timedelta(days=1, hours=6),
        bids=[("bidder_mary", "2500000"), ("bidder_sarah", "2700000"), ("bidder_mary", "2900000")],
    ),
    dict(
        title="Dell Latitude 7490 Laptop (i7, 16GB, 512GB SSD)",
        category="Electronics", seller="seller_rose", state="LIVE",
        description="Excellent condition business laptop, battery holds ~5 hours. Includes charger and bag.",
        location="Mbarara Town", start=D("900000"), reserve=None, inc=D("50000"), extension=5,
        ends_in=timedelta(hours=20),
        bids=[("bidder_isaac", "900000"), ("bidder_emma", "950000")],
    ),
    dict(
        title="Massey Ferguson 290 Tractor with Plough",
        category="Farm Equipment", seller="seller_agnes", state="LIVE",
        description="Reliable 2WD tractor, recently overhauled engine. Comes with a 3-disc plough.",
        location="Kashari, Mbarara", start=D("28000000"), reserve=D("32000000"), inc=D("500000"),
        ends_in=timedelta(days=5),
        bids=[],
    ),
    # ---- Scheduled ----------------------------------------------------
    dict(
        title="Three-Seater Leather Sofa Set (Mahogany Frame)",
        category="Furniture", seller="seller_rose", state="SCHEDULED",
        description="Hand-crafted 3+2+1 sofa set in brown leather, barely used.",
        location="Mbarara Town", start=D("1800000"), reserve=D("2200000"), inc=D("100000"),
        starts_in=timedelta(days=2), duration=timedelta(days=4),
    ),
    dict(
        title="Quarter-Acre Plot, Kakoba Division",
        category="Land & Property", seller="seller_david", state="SCHEDULED",
        description="Titled quarter-acre plot near the main road, water and power nearby. "
                    "Search report available to serious bidders.",
        location="Kakoba, Mbarara", start=D("35000000"), reserve=D("40000000"), inc=D("1000000"),
        starts_in=timedelta(days=4), duration=timedelta(days=7),
    ),
    # ---- Awaiting review / draft / rejected / cancelled ---------------
    dict(
        title="Solar Water Heater System (200L)",
        category="Home & Appliances", seller="seller_agnes", state="SUBMITTED",
        description="Roof-mounted 200-litre solar water heater, installed 2023. Full kit for removal included.",
        location="Mbarara Town", start=D("1500000"), reserve=None, inc=D("50000"),
        starts_in=timedelta(days=3), duration=timedelta(days=5),
    ),
    dict(
        title="Used Office Desks (set of 10)",
        category="Furniture", seller="seller_rose", state="DRAFT",
        description="Ten steel-frame office desks with drawers. Photos to be added.",
        location="Mbarara Town", start=D("1200000"), reserve=None, inc=D("50000"),
        starts_in=timedelta(days=5), duration=timedelta(days=5),
    ),
    dict(
        title="Bulk Lot of Assorted Phones",
        category="Electronics", seller="seller_david", state="REJECTED",
        description="Box of assorted phones, condition unknown.",
        location="Mbarara Town", start=D("500000"), reserve=None, inc=D("20000"),
        starts_in=timedelta(days=2), duration=timedelta(days=3),
        reason="Listing lacks make/model details and photos; please resubmit with an itemised list.",
    ),
    dict(
        title="Queen Size Mattress Set",
        category="Furniture", seller="seller_rose", state="CANCELLED",
        description="Queen mattress with base. Withdrawn by seller.",
        location="Mbarara Town", start=D("600000"), reserve=None, inc=D("30000"),
        starts_in=timedelta(days=6), duration=timedelta(days=3),
        reason="Seller withdrew the item.",
    ),
    # ---- Finished -----------------------------------------------------
    dict(
        title="Bajaj Boxer Motorcycle 2019",
        category="Vehicles", seller="seller_david", state="CLOSED_WON",
        description="Low mileage boda boda, valid insurance, all papers clean.",
        location="Mbarara Town", start=D("3200000"), reserve=D("3500000"), inc=D("100000"),
        bids=[("bidder_emma", "3300000"), ("bidder_john", "3500000"),
              ("bidder_emma", "3700000"), ("bidder_sarah", "3900000")],
    ),
    dict(
        title="Hisense 55-inch Smart TV",
        category="Electronics", seller="seller_agnes", state="COMPLETED",
        description="4K smart TV with wall bracket, 14 months old.",
        location="Mbarara Town", start=D("1100000"), reserve=None, inc=D("50000"),
        bids=[("bidder_mary", "1100000"), ("bidder_isaac", "1200000"), ("bidder_mary", "1300000")],
        payment=("Mobile Money", "MM-48210937", "Paid via MTN MoMo; confirmed by officer."),
    ),
    dict(
        title="Vintage Singer Sewing Machine",
        category="Home & Appliances", seller="seller_rose", state="CLOSED_NO_WINNER",
        description="Antique hand-crank Singer machine in working order.",
        location="Mbarara Town", start=D("400000"), reserve=D("700000"), inc=D("20000"),
        bids=[("bidder_sarah", "400000"), ("bidder_isaac", "450000")],
    ),
]


class Command(BaseCommand):
    help = "Seed demo users, categories, auctions, bids and payments (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--password", default=os.environ.get("SEED_PASSWORD", "Seed@12345"),
                            help="Password for all seeded accounts (or set SEED_PASSWORD).")
        parser.add_argument("--force", action="store_true", help="Allow running when DEBUG is False.")

    @transaction.atomic
    def handle(self, *args, **opts):
        if not settings.DEBUG and not opts["force"]:
            raise CommandError("Refusing to seed demo data with DEBUG=False. Use --force if you really mean it.")

        # Notifications are saved to the DB before being handed to Celery. Run the
        # (log-only) delivery task in-process so seeding works without Redis/broker.
        from config.celery import app as celery_app
        celery_app.conf.task_always_eager = True
        logging.disable(logging.INFO)  # silence per-notification Celery log lines

        self.password = opts["password"]
        self.now = timezone.now()
        self.users = {}

        self._categories()
        self._users()
        self._auctions()

        self.stdout.write(self.style.SUCCESS("\nSeed complete."))
        self.stdout.write(f"All seeded accounts use password: {self.password}")
        self.stdout.write("  Admin:   seed_admin (staff + superuser)")
        self.stdout.write("  Officer: officer_moses")
        self.stdout.write("  Sellers: " + ", ".join(s[0] for s in SELLERS))
        self.stdout.write("  Bidders: " + ", ".join(b[0] for b in BIDDERS))

    # ------------------------------------------------------------------
    def _categories(self):
        self.categories = {}
        for name in CATEGORIES:
            self.categories[name], _ = AuctionCategory.objects.get_or_create(name=name)
        self.stdout.write(f"Categories: {len(self.categories)}")

    def _make_user(self, username, first, last, role, phone, **extra):
        user, created = User.objects.get_or_create(
            username=username,
            defaults=dict(first_name=first, last_name=last, email=f"{username}@example.com",
                          role=role, phone_number=phone, **extra),
        )
        if created:
            user.set_password(self.password)
            user.save(update_fields=["password"])
        self.users[username] = user
        return user, created

    def _users(self):
        admin, _ = self._make_user("seed_admin", "Grace", "Atuhaire", User.Role.ADMIN, "+256700000001",
                                   is_staff=True, is_superuser=True)
        officer, _ = self._make_user("officer_moses", "Moses", "Tumusiime", User.Role.OFFICER, "+256700000002",
                                     is_staff=True)
        self.officer = officer

        for username, first, last, phone, city, verified in SELLERS:
            user, created = self._make_user(username, first, last, User.Role.SELLER, phone)
            profile = user.seller_profile
            if created:
                profile.city = city
                profile.save(update_fields=["city", "updated_at"])
                if verified:
                    verify_profile(profile, actor=officer, reason="Seed data")

        for username, first, last, phone, location, state in BIDDERS:
            user, created = self._make_user(username, first, last, User.Role.BIDDER, phone)
            profile = user.bidder_profile
            if created:
                profile.location = location
                profile.save(update_fields=["location", "updated_at"])
                if state in ("verified", "suspended"):
                    verify_profile(profile, actor=officer, reason="Seed data")
                if state == "suspended":
                    suspend_bidder(profile, actor=officer, reason="Repeated non-payment on previous auctions (demo).")

        self.stdout.write(f"Users: {2 + len(SELLERS) + len(BIDDERS)}")

    # ------------------------------------------------------------------
    def _auctions(self):
        created_count = 0
        for spec in AUCTIONS:
            if Auction.objects.filter(title=spec["title"]).exists():
                self.stdout.write(f"  skip (exists): {spec['title']}")
                continue
            self._build_auction(spec)
            created_count += 1
            self.stdout.write(f"  + {spec['state']:<17} {spec['title']}")
        self.stdout.write(f"Auctions created: {created_count}")

    def _build_auction(self, spec):
        now, officer = self.now, self.officer
        state = spec["state"]
        seller_user = self.users[spec["seller"]]

        # Choose a start/end window that lets the auction legitimately reach its state.
        if "starts_in" in spec:
            start = now + spec["starts_in"]
            end = start + spec["duration"]
        elif state == "LIVE":
            start = now - timedelta(days=2)
            end = now + spec["ends_in"]
        else:  # finished auctions: open now-ish, then we move end_time into the past
            start = now - timedelta(days=10)
            end = now + timedelta(hours=1)

        auction = Auction.objects.create(
            seller=seller_user.seller_profile, category=self.categories[spec["category"]],
            title=spec["title"], description=spec["description"], location=spec["location"],
            starting_price=spec["start"], reserve_price=spec["reserve"], min_increment=spec["inc"],
            extension_minutes=spec.get("extension", 0), start_time=start, end_time=end,
        )

        if state == "DRAFT":
            return
        if state == "CANCELLED":
            auction_services.submit_for_review(auction, actor=seller_user)
            auction = auction_services.approve(auction, actor=officer)
            auction_services.cancel(auction, actor=seller_user, reason=spec["reason"])
            return

        auction = auction_services.submit_for_review(auction, actor=seller_user)
        if state == "SUBMITTED":
            return
        if state == "REJECTED":
            auction_services.reject(auction, actor=officer, reason=spec["reason"])
            return

        auction = auction_services.approve(auction, actor=officer)  # -> SCHEDULED
        if state == "SCHEDULED":
            return

        # Everything else needs to be LIVE first so bids go through place_bid().
        auction = auction_services.activate_if_due(auction)
        for bidder, amount in spec.get("bids", []):
            bidding_services.place_bid(auction, bidder_user=self.users[bidder], amount=D(amount))

        if "invalidate_bid" in spec:
            seq, reason = spec["invalidate_bid"]
            bid = Bid.objects.get(auction=auction, sequence_no=seq)
            bidding_services.invalidate_bid(bid, actor=officer, reason=reason)

        if state == "LIVE":
            return

        # Finished auctions: move end_time into the past, then close via the real closing logic.
        Auction.objects.filter(pk=auction.pk).update(end_time=now - timedelta(hours=1))
        auction.refresh_from_db()
        auction = bidding_services.close_auction(auction, actor=None)

        if state == "COMPLETED":
            method, reference, notes = spec["payment"]
            payment_services.mark_paid(auction.payment, actor=officer, method=method,
                                       reference=reference, notes=notes)
            auction.refresh_from_db()
            auction_services.transition(auction, Auction.Status.COMPLETED, actor=officer,
                                        reason="Item handed over to buyer")
