"""Daisy Bell (Harry Dacre, 1892): the chorus as printed by T. B. Harms & Co., New York, 1892.

Transcribed from the University of Wisconsin scan of the first edition (G major, 3/4, "A little
faster"): https://digital.library.wisc.edu/1711.dl/N22C7MP6ACVCM8H
The melody is in the score's register (MIDI notes); the voice sings it an octave lower.
The harmony is read from the printed piano part, one chord a bar.
The words and the music were published in 1892 and are in the public domain.
"""

# (syllable, MIDI note or a list for a triplet, beats). None as the syllable is a rest.
MELODY = [
    ('Dai', 74, 3), ('sy', 71, 3), ('Dai', 67, 3), ('sy', 62, 2), (None, None, 1),            # 1-4
    ('Give', 64, 1), ('me', 66, 1), ('your', 67, 1), ('an', 64, 2), ('swer', 67, 1),           # 5-6
    ('do', 62, 5), (None, None, 1),                                                           # 7-8
    ("I'm", 69, 3), ('half', 74, 3), ('cra', 71, 3), ('zy', 67, 2), (None, None, 1),          # 9-12
    ('All', 64, 1), ('for', 66, 1), ('the', 67, 1), ('love', 69, 2), ('of', 71, 1),           # 13-14
    ('you', 69, 4), (None, None, 1), ('It', 71, 1),                                           # 15-16
    ("won't", 72, 1), ('be', 71, 1), ('a', 69, 1), ('styl', 74, 2), ('ish', 71, 1),           # 17-18
    ('mar', [69, 71, 69], 1), ('riage', 67, 4), ('I', 69, 1),                                 # 19-20
    ("can't", 71, 2), ('af', 67, 1), ('ford', 64, 2), ('a', 67, 1),                           # 21-22
    ('car', 64, 1), ('riage', 62, 3), (None, None, 1), ('But', 62, 1),                        # 23-24
    ("you'll", 67, 2), ('look', 71, 1), ('sweet', 69, 1), (None, None, 2),                    # 25-26
    ('On', 67, 2), ('the', 71, 1), ('seat', 69, 1), (None, None, 1), ('Of', 71, 0.5), ('a2', 72, 0.5),  # 27-28
    ('bi', 74, 1), ('cy', 71, 1), ('cle', 67, 1), ('built', 69, 2), ('for2', 62, 1),          # 29-30
    ('two', 67, 4), (None, None, 2),                                                          # 31-32
]

# one chord a bar, bars 1-32, from the printed accompaniment
HARMONY = ['G', 'G', 'G', 'G', 'C', 'C', 'G', 'G',
           'D7', 'D7', 'G', 'G', 'A7', 'A7', 'D7', 'D7',
           'D7', 'D7', 'G', 'G', 'C', 'C', 'G', 'G',
           'G', 'D7', 'G', 'D7', 'G', 'D7', 'G', 'G']

CHORDS = {'G': (7, [7, 11, 2]), 'C': (0, [0, 4, 7]), 'D7': (2, [2, 6, 9, 0]), 'A7': (9, [9, 1, 4, 7])}

# accents printed on "sweet" and "seat"; dynamics: mf at the chorus, f at "But you'll look sweet"
ACCENT = {'sweet', 'seat'}
FORTE_FROM = 'But'

# the words as shown, one line each; '|' splits the syllables in the order they are sung
LYRICS = [
    "Dai|sy, |Dai|sy,",
    "Give |me |your |an|swer, |do!",
    "I'm |half |cra|zy,",
    "All |for |the |love |of |you!",
    "It |won't |be |a |styl|ish |mar|riage,",
    "I |can't |af|ford |a |car|riage,",
    "But |you'll |look |sweet",
    "On |the |seat",
    "Of |a |bi|cy|cle |built |for |two!",
]
LYRIC_ROWS = [4, 5, 8, 9, 12, 13, 16, 17, 18]

# (onset consonants, nucleus, coda consonants) for every syllable key above
LEXICON = {
    'Dai': (['D'], 'EY', []), 'sy': (['Z'], 'IY', []), 'Give': (['G'], 'IH', ['V']),
    'me': (['M'], 'IY', []), 'your': (['Y'], 'AO', ['R']), 'an': ([], 'AE', ['N']),
    'swer': (['S'], 'ER', []), 'do': (['D'], 'UW', []), "I'm": ([], 'AY', ['M']),
    'half': (['HH'], 'AE', ['F']), 'cra': (['K', 'R'], 'EY', []), 'zy': (['Z'], 'IY', []),
    'All': ([], 'AO', ['L']), 'for': (['F'], 'AO', ['R']), 'the': (['DH'], 'AX', []),
    'love': (['L'], 'AH', ['V']), 'of': ([], 'AH', ['V']), 'you': (['Y'], 'UW', []),
    'It': ([], 'IH', ['T']), "won't": (['W'], 'OW', ['N', 'T']), 'be': (['B'], 'IY', []),
    'a': ([], 'AH', []), 'styl': (['S', 'T'], 'AY', ['L']), 'ish': ([], 'IH', ['SH']),
    'mar': (['M'], 'AE', []), 'riage': (['R'], 'IH', ['JH']), 'I': ([], 'AY', []),
    "can't": (['K'], 'AE', ['N', 'T']), 'af': ([], 'AX', []), 'ford': (['F'], 'AO', ['R', 'D']),
    'car': (['K'], 'AE', []), 'But': (['B'], 'AH', ['T']), "you'll": (['Y'], 'UW', ['L']),
    'look': (['L'], 'UH', ['K']), 'sweet': (['S', 'W'], 'IY', ['T']), 'On': ([], 'AA', ['N']),
    'seat': (['S'], 'IY', ['T']), 'Of': ([], 'AH', ['V']), 'a2': ([], 'AH', []),
    'bi': (['B'], 'AY', []), 'cy': (['S'], 'IH', []), 'cle': (['K'], 'AX', ['L']),
    'built': (['B'], 'IH', ['L', 'T']), 'for2': (['F'], 'AO', ['R']), 'two': (['T'], 'UW', []),
}

# the intro, typed while the SIDs play the whole chorus (part 1). The first paragraph during the
# first half, with two corrections against the caption it comes from: "Lochbaum", not "Lockbaum",
# and "an IBM mainframe at Bell Labs", as the sources name the IBM 704 and the 7090 (the IBM 7094
# was first installed in September 1962). The second paragraph during the second half, with
# "an 8-bit computer" for readers who do not know the MEGA65.
INTRO = ("In 1961, an IBM mainframe at Bell Labs became the first computer to sing, singing "
         "this song. Vocals were programmed by John Kelly and Carol Lochbaum and the "
         "accompaniment was programmed by Max Mathews.")
INTRO2 = ("In 2026 a MEGA65, an 8-bit computer, was also programmed to synthesize it, and became "
          "a machine that sings.")
