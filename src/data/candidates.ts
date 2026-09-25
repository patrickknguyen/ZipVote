import { Candidate } from "@/types";

/**
 * November 3, 2026 general election candidates for zip 02144 (Somerville, MA).
 * Ids and races must match agents/config.py.
 *
 * Keep this file to facts about each person's role. Anything about their
 * positions belongs in the sourced pipeline output (data/processed/), where
 * every claim has a quote and a link.
 *
 * Independents marked "confirm" came from Ballotpedia/Wikipedia and still
 * need checking against the Secretary of the Commonwealth's official list.
 */
export const candidates: Candidate[] = [
    // ── U.S. Senate ─────────────────────────────────────────────────────────
    {
        id: "markey",
        name: "Ed Markey",
        party: "Democrat",
        race: "senate",
        bio: "U.S. Senator from Massachusetts since 2013. Represented Massachusetts in the U.S. House from 1976 to 2013.",
        imageInitials: "EM",
        imageUrl: "https://upload.wikimedia.org/wikipedia/commons/thumb/9/94/Edward_Markey%2C_official_portrait%2C_114th_Congress.jpg/330px-Edward_Markey%2C_official_portrait%2C_114th_Congress.jpg",
        wikipedia: "https://en.wikipedia.org/wiki/Ed_Markey",
        ballotpedia: "https://ballotpedia.org/Ed_Markey",
        color: "bg-indigo-500",
    },
    {
        id: "deaton",
        name: "John Deaton",
        party: "Republican",
        race: "senate",
        bio: "Attorney and Republican nominee for U.S. Senate. Ran for U.S. Senate in Massachusetts in 2024.",
        imageInitials: "JD",
        ballotpedia: "https://ballotpedia.org/John_Deaton_(Massachusetts)",
        color: "bg-red-500",
    },
    // ── U.S. House, MA-7 ────────────────────────────────────────────────────
    {
        id: "pressley",
        name: "Ayanna Pressley",
        party: "Democrat",
        race: "house_ma7",
        bio: "U.S. Representative for Massachusetts's 7th District since 2019. Previously served on the Boston City Council.",
        imageInitials: "AP",
        imageUrl: "https://upload.wikimedia.org/wikipedia/commons/thumb/5/57/Rep._Ayanna_Pressley%2C_117th_Congress.jpg/330px-Rep._Ayanna_Pressley%2C_117th_Congress.jpg",
        wikipedia: "https://en.wikipedia.org/wiki/Ayanna_Pressley",
        ballotpedia: "https://ballotpedia.org/Ayanna_Pressley",
        color: "bg-indigo-500",
    },
    {
        id: "linardon", // confirm
        name: "Kelechi Linardon",
        party: "Independent",
        race: "house_ma7",
        bio: "Independent candidate for U.S. House, Massachusetts 7th District.",
        imageInitials: "KL",
        color: "bg-slate-500",
    },
    // ── Governor ────────────────────────────────────────────────────────────
    {
        id: "healey",
        name: "Maura Healey",
        party: "Democrat",
        race: "governor",
        bio: "Governor of Massachusetts since 2023. Previously Massachusetts Attorney General.",
        imageInitials: "MH",
        wikipedia: "https://en.wikipedia.org/wiki/Maura_Healey",
        ballotpedia: "https://ballotpedia.org/Maura_Healey",
        color: "bg-indigo-500",
    },
    {
        id: "minogue",
        name: "Mike Minogue",
        party: "Republican",
        race: "governor",
        bio: "Republican nominee for Governor of Massachusetts.",
        imageInitials: "MM",
        color: "bg-red-500",
    },
    {
        id: "james", // confirm
        name: "Andrea James",
        party: "Independent",
        race: "governor",
        bio: "Independent candidate for Governor of Massachusetts.",
        imageInitials: "AJ",
        color: "bg-slate-500",
    },
    {
        id: "kokonezis_hanino", // confirm
        name: "Muhammed Kokonezis-Hanino",
        party: "Independent",
        race: "governor",
        bio: "Independent candidate for Governor of Massachusetts.",
        imageInitials: "MK",
        color: "bg-slate-500",
    },
];
