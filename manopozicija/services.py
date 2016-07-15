import urllib
import itertools

from django.utils import timezone
from django.db.models import F, Case, When, Count, Sum, Avg
from django.utils.translation import ugettext
from django.contrib.contenttypes.models import ContentType

from manopozicija import models


def create_event(user, topic, event_data):
    source_link = event_data.pop('source_link')
    event_data['user'] = user
    event_data['type'] = models.Event.DOCUMENT
    event_data['position'] = 0
    event_data['source_title'] = get_title_from_link(source_link)
    event, created = models.Event.objects.get_or_create(source_link=source_link, defaults=event_data)

    is_curator = is_topic_curator(user, topic)
    approved = timezone.now() if is_curator else None

    post = models.Post.objects.create(
        body=topic.default_body,
        topic=topic,
        position=0,
        approved=approved,
        timestamp=event.timestamp,
        upvotes=0,
        content_object=event,
    )

    if is_curator:
        # Automatically approve posts created by topic curators.
        models.PostLog.objects.create(user=user, post=post, action=models.PostLog.VOTE, vote=1)

    return event


def create_quote(user, topic, source: dict, quote: dict, arguments: list):
    source['actor_title'] = source['actor'].title
    source['source_title'] = get_title_from_link(source['source_link'])
    source, created = models.Source.objects.get_or_create(
        actor=source['actor'],
        source_link=source['source_link'],
        defaults=source,
    )

    quote = models.Quote.objects.create(user=user, source=source, **quote)

    is_curator = is_topic_curator(user, topic)
    approved = timezone.now() if is_curator else None

    post = models.Post.objects.create(
        body=topic.default_body,
        topic=topic,
        actor=source.actor,
        position=get_quote_position(topic, quote),
        approved=approved,
        timestamp=source.timestamp,
        upvotes=0,
        content_object=quote,
    )

    if is_curator:
        # Automatically approve posts created by topic curators.
        models.PostLog.objects.create(user=user, post=post, action=models.PostLog.VOTE, vote=1)

    for argument in arguments:
        if argument.get('title'):
            models.Argument.objects.create(topic=topic, post=post, quote=quote, **argument)

    source.position = get_source_position(topic, source)
    source.save()

    return quote


def create_curator(user, topic, user_data: dict, curator: dict):
    curator, created = models.Curator.objects.get_or_create(user=user, defaults=curator)
    if user_data:
        user.first_name = user_data['first_name']
        user.last_name = user_data['last_name']
        user.save()

    # Add new curator as a topic post to be approved by other curators.
    models.Post.objects.create(
        body=topic.default_body,
        topic=topic,
        actor=None,
        position=0,
        approved=None,
        timestamp=timezone.now(),
        upvotes=0,
        content_object=curator,
    )

    return curator


def is_topic_curator(user, topic):
    return models.TopicCurator.objects.filter(user=user, topic=topic, approved__isnull=False).exists()


def get_title_from_link(link):
    title = urllib.parse.urlparse(link).netloc
    if title.startswith('www.'):
        title = title[4:]
    return title


def get_source_position(topic, source):
    agg = (
        models.Argument.objects.
        filter(topic=topic, quote__source=source, post__approved__isnull=False).
        aggregate(position=Avg(Case(
            When(counterargument=True, then=F('position') * -1),
            default=F('position')
        )))
    )
    return agg['position'] or 0


def get_quote_position(topic, quote):
    agg = (
        models.Argument.objects.
        filter(topic=topic, quote=quote, post__approved__isnull=False).
        aggregate(position=Avg(Case(
            When(counterargument=True, then=F('position') * -1),
            default=F('position')
        )))
    )
    return agg['position'] or 0


def get_topic_arguments(topic):
    return (
        models.Argument.objects.
        values('position', 'title').
        filter(topic=topic, counterargument=False, post__approved__isnull=False).
        annotate(count=Count('title')).
        order_by('-position', '-count', 'title')
    )


def get_topic_posts(topic, queue=False):
    result = []

    if queue:
        qs = (
            models.Post.objects.
            filter(topic=topic, approved__isnull=True).
            order_by('-created')
        )
    else:
        curator_type = ContentType.objects.get(app_label='manopozicija', model='curator')
        qs = (
            models.Post.objects.
            exclude(content_type=curator_type).
            filter(topic=topic, approved__isnull=False).
            order_by('-timestamp')
        )

    groups = itertools.groupby(qs, key=lambda x: (x.content_type.app_label, x.content_type.model))
    for content_type, posts in groups:
        if content_type == ('manopozicija', 'event'):
            for post in posts:
                result.append({
                    'type': post.content_type.model,
                    'post': post,
                    'event': post.content_object,
                })
        elif content_type == ('manopozicija', 'quote'):
            for _, quotes in itertools.groupby(posts, key=lambda x: x.content_object.source.pk):
                quotes = list(quotes)
                result.append({
                    'type': quotes[0].content_type.model,
                    'source': quotes[0].content_object.source,
                    'quotes': [(x, x.content_object) for x in quotes],
                })
        elif content_type == ('manopozicija', 'curator'):
            for post in posts:
                result.append({
                    'type': post.content_type.model,
                    'post': post,
                    'curator': post.content_object,
                })
        else:
            raise ValueError('Unknown content type: %r' % (content_type,))
    return result


def _format_position(position):
    if position > 0.5:
        return '(y)'
    elif position < -0.5:
        return '(n)'
    else:
        return '(-)'


def _align_both_sides(left, rigth, width):
    return left + rigth.rjust(width - len(left))


def get_post_votes_display(post):
    if post.approved:
        upvotes = post.upvotes
        downvotes = post.downvotes
        return upvotes if upvotes >= downvotes else -downvotes
    else:
        upvotes = post.curator_upvotes
        downvotes = post.curator_downvotes
        return upvotes if upvotes > downvotes else -downvotes


def dump_topic_posts(topic, **kwargs):
    width = 108
    middle = width - 12
    result = []
    for i, row in enumerate(get_topic_posts(topic, **kwargs)):
        if i > 0:
            result.append(' | ' + ' ' * (width - 3))
        if row['type'] == 'event':
            result.append(' o  {position} {middle} ({votes})'.format(
                position=_format_position(row['event'].position),
                middle=_align_both_sides(
                    row['event'].title,
                    '%s %s' % (row['event'].source_title, row['post'].timestamp.strftime('%Y-%m-%d')),
                    middle,
                ),
                votes=get_post_votes_display(row['post']),
            ))
        elif row['type'] == 'curator':
            result.append('( ) {middle} ({votes})'.format(
                middle=_align_both_sides(
                    '%s (%s)' % (
                        row['curator'].user.get_full_name(),
                        row['curator'].title,
                    ),
                    ugettext("naujas temos kuratorius"),
                    middle,
                ),
                votes=get_post_votes_display(row['post']),
            ))
        else:
            result.append('( ) {position} {middle}    '.format(
                position=_format_position(row['source'].position),
                middle=_align_both_sides(
                    '%s (%s)' % (
                        ' '.join([row['source'].actor.first_name, row['source'].actor.last_name]),
                        row['source'].actor_title,
                    ),
                    '%s %s' % (
                        row['source'].source_title,
                        row['source'].timestamp.strftime('%Y-%m-%d'),
                    ),
                    middle,
                ),
            ))
            for post, quote in row['quotes']:
                votes = get_post_votes_display(post)
                result.append(' |      %s' % _align_both_sides(quote.text, '(%s)' % votes, middle + 4))
                for argument in quote.argument_set.all():
                    if argument.counterargument and argument.counterargument_title:
                        counterargument = ' < ' + argument.counterargument_title
                    elif argument.counterargument:
                        counterargument = ' < (counterargument)'
                    else:
                        counterargument = ''
                    result.append(_align_both_sides(' |      - {position} {argument}{counterargument}'.format(
                        position=_format_position(argument.position),
                        argument=argument.title,
                        counterargument=counterargument,
                    ), '', width))
    return '\n'.join(result)


def get_post_votes(post):
    agg = models.UserPosition.objects.filter(post=post).aggregate(
        upvotes=Sum(Case(When(position__gt=0, then=F('position')), default=0)),
        downvotes=Sum(Case(When(position__lt=0, then=F('position')), default=0)),
    )
    return agg['upvotes'] or 0, abs(agg['downvotes'] or 0)


def update_user_position(user, post, vote: int):
    models.UserPosition.objects.update_or_create(user=user, post=post, defaults={'position': vote})
    post.upvotes, post.downvotes = get_post_votes(post)
    post.save()
    return post.upvotes, post.downvotes


def get_curator_votes(post):
    agg = models.PostLog.objects.filter(post=post).aggregate(
        upvotes=Sum(Case(When(vote__gt=0, then=F('vote')), default=0)),
        downvotes=Sum(Case(When(vote__lt=0, then=F('vote')), default=0)),
    )
    return agg['upvotes'] or 0, abs(agg['downvotes'] or 0)


def update_curator_position(user, post, vote: int):
    models.PostLog.objects.update_or_create(user=user, post=post, action=models.PostLog.VOTE, defaults={'vote': vote})

    post.curator_upvotes, post.curator_downvotes = get_curator_votes(post)
    if post.curator_upvotes > post.curator_downvotes:
        post.approved = timezone.now()
    else:
        post.approved = None
    post.save()

    curator_type = ContentType.objects.get(app_label='manopozicija', model='curator')
    if post.content_type == curator_type:
        update_curator_application(post.content_object.user, post.topic, post.approved)

    return post.curator_upvotes, post.curator_downvotes


def update_curator_application(user, topic, approved):
    models.TopicCurator.objects.update_or_create(user=user, topic=topic, defaults={'approved': approved})


def get_user_topic_votes(user, topic):
    return dict(
        models.UserPosition.objects.
        filter(user=user, post__topic=topic).
        values_list('post_id', 'position')
    )


def get_curator_topic_votes(user, topic):
    return dict(
        models.PostLog.objects.
        filter(user=user, post__topic=topic, action=models.PostLog.VOTE).
        values_list('post_id', 'vote')
    )
