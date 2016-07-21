import itertools

from django.db.models import Q

from manopozicija import models
from manopozicija import services


def _get_position_image(position, positive, negative, neutral):
    if position > 0.5:
        return positive
    elif position < -0.5:
        return negative
    else:
        return neutral


def _get_post_context(post, user_votes, curator_votes):
    votes = user_votes if post.approved else curator_votes
    return {
        'id': post.pk,
        'votes': services.get_post_votes_display(post),
        'user': {
            'upvote': 'active' if votes.get(post.pk, 0) > 0 else '',
            'downvote': 'active' if votes.get(post.pk, 0) < 0 else '',
        },
        'save_vote': 'manopozicija.save_user_vote' if post.approved else 'manopozicija.save_curator_vote',
    }


def get_posts(user, topic, posts):
    result = []
    user_votes = services.get_user_topic_votes(user, topic)
    curator_votes = services.get_curator_topic_votes(user, topic)
    for post in posts:
        if post['type'] == 'event':
            event = post['event']
            result.append({
                'type': 'event',
                'post': _get_post_context(post['post'], user_votes, curator_votes),
                'event': {
                    'position_image': _get_position_image(
                        event.position,
                        'img/event-positive.png',
                        'img/event-negative.png',
                        'img/event-neutral.png',
                    ),
                    'name': event.title,
                    'timestamp': event.timestamp.strftime('%Y-%m-%d'),
                    'source': {
                        'link': event.source_link,
                        'name': event.source_title,
                    },
                },
            })
        else:
            source = post['source']
            actor = source.actor
            result.append({
                'type': 'quotes',
                'source': {
                    'link': source.source_link,
                    'name': source.source_title,
                    'actor': {
                        'name': str(actor),
                        'title': source.actor_title or actor.title,
                        'photo': actor.photo,
                        'position_image': _get_position_image(
                            source.position,
                            'img/actor-positive.png',
                            'img/actor-negative.png',
                            'img/actor-neutral.png',
                        ),
                    },
                },
                'quotes': [{
                    'text': quote.text,
                    'post': _get_post_context(post, user_votes, curator_votes),
                    'vote': {
                        'img': {
                            'top': 'img/thumb-up.png',
                            'bottom': 'img/thumb-down.png',
                        },
                    },
                    'arguments': [{
                        'name': argument.title,
                        'classes': 'text-%s' % ('danger' if argument.position < 0 else 'success'),
                        'counterargument': {
                            'classes': 'glyphicon glyphicon-%s' % ('remove' if argument.counterargument else 'tag'),
                        }
                    } for argument in quote.postargument_set.order_by('pk')],
                } for post, quote in post['quotes']],
            })
    return result


def get_arguments(arguments):
    groups = itertools.groupby(arguments, key=lambda x: x['position'])
    positive = list(next(groups, (+1, []))[1])
    negative = list(next(groups, (-1, []))[1])
    return list(itertools.zip_longest(positive, negative))


def _actor_details(groups, actors, actor_id, distance):
    if actor_id:
        return {
            'actor': actors[actor_id],
            'group': groups.get(actor_id),
            'distance': distance,
        }
    else:
        return None


def get_positions(group, user, limit=20):
    threshold = 0.4
    actors = {x.pk: x for x in group.members.all()}
    groups = {
        x.actor_id: x.group for x in (
            models.Member.objects.filter(
                Q(until__lte=group.timestamp) | Q(until__isnull=True),
                since__gte=group.timestamp,
                actor__ingroup=group,
                group__title='politinė partija',
            ).
            select_related('group').
            order_by('-since')
        )
    }
    positions = services.compare_positions(group, user)
    compat = ((x, d) for x, d in positions if d < threshold)
    incompat = ((x, d) for x, d in reversed(positions) if d >= threshold)
    result = []
    for i, (left, right) in zip(range(limit), itertools.zip_longest(compat, incompat, fillvalue=(None, None))):
        result.append((
            _actor_details(groups, actors, *left),
            _actor_details(groups, actors, *right),
        ))
    return result
