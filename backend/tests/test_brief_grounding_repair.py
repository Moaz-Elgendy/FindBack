"""Validation repairs what is safe to repair instead of failing the whole brief.

Found by the five live evaluations in docs/BRIEF_V2_VERIFICATION.md: 2 of 5
runs fell back to a placeholder brief (which search then hides) for reasons the
code could have resolved by itself.

    * `entities.numbers` came back as integers, `title` came back too long
      -> repaired before schema validation;
    * an STT-guessed name ("Pickabla", "Fine skills") sat in `entities`
      -> dropped from the stored entities and tags. The brief is rejected only
         if the prose still presents that name as a name.
"""
import asyncio

import pytest

from app.services import brief_v2
from test_brief_v2 import evidence, payload


def _ev(text="I'm pickabla. It helps frontend work and Fine skills too."):
    return dict(evidence(), transcript=[{'start': 5, 'end': 15, 'text': text}])


def test_numeric_entities_are_coerced_to_strings():
    data = dict(payload(), entities={'tools_products': ['Claude Code'],
                                     'people_orgs': [], 'numbers': [5, 2, 3.5]})
    assert brief_v2.validate(data, evidence()).entities.numbers == ['5', '2', '3.5']


def test_an_overlong_title_is_clipped_at_a_word_boundary():
    words = ' '.join(f'word{i}' for i in range(30))
    assert len(words) > 80
    title = brief_v2.validate(dict(payload(), title=words), evidence()).title
    assert 0 < len(title) <= 80
    assert words.startswith(title) and not title.endswith(' ')
    assert words[len(title)] == ' '  # cut between words, not inside one


def test_an_stt_guessed_name_is_dropped_not_fatal():
    data = dict(payload(), entities={'tools_products': ['Claude Code', 'Pickabla'],
                                     'people_orgs': ['Fine skills'], 'numbers': []},
                tags=payload()['tags'] + ['pickabla'])
    result = brief_v2.validate(data, _ev())
    assert result.entities.tools_products == ['Claude Code']
    assert result.entities.people_orgs == []
    assert 'pickabla' not in result.tags and len(result.tags) >= 15


def test_a_written_source_still_confirms_the_name():
    data = dict(payload(), entities={'tools_products': ['Pickabla'],
                                     'people_orgs': [], 'numbers': []})
    result = brief_v2.validate(data, dict(_ev(), ocr_text='PICKABLA'))
    assert result.entities.tools_products == ['Pickabla']


def test_a_generic_lowercase_phrase_in_entities_is_pruned_but_may_stay_in_prose():
    data = dict(payload(), entities={'tools_products': ['Claude Code', 'skill library'],
                                     'people_orgs': [], 'numbers': []},
                instant_brief='Claude searches the skill library for you.')
    result = brief_v2.validate(data, _ev('Claude searches the skill library.'))
    assert result.entities.tools_products == ['Claude Code']
    assert 'skill library' in result.instant_brief


def test_a_guessed_name_that_the_prose_still_asserts_is_rejected():
    data = dict(payload(), instant_brief='Pickabla helps with frontend work.',
                entities={'tools_products': ['Claude Code', 'Pickabla'],
                          'people_orgs': [], 'numbers': []})
    with pytest.raises(ValueError, match='STT guess'):
        brief_v2.validate(data, _ev())


def test_the_repair_attempt_still_names_the_confirmed_entities():
    class Gateway:
        calls = []

        async def generate_json(self, system, user, **kwargs):
            self.calls.append(user)
            if len(self.calls) == 1:
                return dict(payload(), instant_brief='Pickabla helps with frontend work.',
                            entities={'tools_products': ['Claude Code', 'Pickabla'],
                                      'people_orgs': [], 'numbers': []})
            assert 'Keep only these confirmed named entities: ["Claude Code"]' in user
            return payload()

    gateway = Gateway()
    result = asyncio.run(brief_v2.extract(_ev(), gateway))
    assert result.entities.tools_products == ['Claude Code']
    assert len(gateway.calls) == 2

@pytest.mark.parametrize('name', ['pickabla', 'fine skills'])
def test_lowercase_guessed_names_in_prose_are_rejected(name):
    data = dict(payload(), instant_brief=f'{name} helps with frontend work.',
                entities={'tools_products': [name], 'people_orgs': [], 'numbers': []})
    with pytest.raises(ValueError, match='STT guess'):
        brief_v2.validate(data, _ev())


def test_tags_containing_a_dropped_name_are_removed():
    data = dict(payload(), entities={'tools_products': ['pickabla'],
                                     'people_orgs': [], 'numbers': []},
                tags=payload()['tags'] + ['pickabla frontend', 'fine pickabla skills'])
    result = brief_v2.validate(data, _ev())
    assert all('pickabla' not in tag for tag in result.tags)
