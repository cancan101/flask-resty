import flask
from flask.ext.resty import (
    Api, ApiError, AuthenticationBase, GenericModelView,
    HasAnyCredentialsAuthorization,
)
from marshmallow import fields, Schema
import pytest
from sqlalchemy import Column, Integer, String

import helpers

# -----------------------------------------------------------------------------


@pytest.yield_fixture
def models(db):
    class Widget(db.Model):
        __tablename__ = 'widgets'

        id = Column(Integer, primary_key=True)
        owner_id = Column(String)
        name = Column(String)

    db.create_all()

    yield {
        'widget': Widget,
    }

    db.drop_all()


@pytest.fixture
def schemas():
    class WidgetSchema(Schema):
        id = fields.Integer(as_string=True)
        owner_id = fields.String()
        name = fields.String()

    return {
        'widget': WidgetSchema(),
    }


@pytest.fixture
def auth():
    class FakeAuthentication(AuthenticationBase):
        def get_request_credentials(self):
            return flask.request.args.get('user_id')

    class UserAuthorization(HasAnyCredentialsAuthorization):
        def filter_query(self, query, view):
            return query.filter(
                (view.model.owner_id == self.get_request_credentials()) |
                (view.model.owner_id == None),  # noqa
            )

        def authorize_save_item(self, item):
            return self.authorize_modify_item(item)

        def authorize_update_item(self, item, data):
            return self.authorize_modify_item(item)

        def authorize_delete_item(self, item):
            return self.authorize_modify_item(item)

        def authorize_modify_item(self, item):
            if item.owner_id != self.get_request_credentials():
                raise ApiError(403, {'code': 'invalid_user'})

    return {
        'authentication': FakeAuthentication(),
        'authorization': UserAuthorization(),
    }


@pytest.fixture(autouse=True)
def routes(app, models, schemas, auth):
    class WidgetViewBase(GenericModelView):
        model = models['widget']
        schema = schemas['widget']

        authentication = auth['authentication']
        authorization = auth['authorization']

    class WidgetListView(WidgetViewBase):
        def get(self):
            return self.list()

        def post(self):
            return self.create()

    class WidgetView(WidgetViewBase):
        def get(self, id):
            return self.retrieve(id)

        def patch(self, id):
            return self.update(id, partial=True)

        def delete(self, id):
            return self.destroy(id)

    api = Api(app, '/api')
    api.add_resource(
        '/widgets', WidgetListView, WidgetView, id_rule='<int:id>'
    )


@pytest.fixture(autouse=True)
def data(db, models):
    def create_widget(owner_id, name):
        widget = models['widget']()
        widget.owner_id = owner_id
        widget.name = name
        return widget

    db.session.add_all((
        create_widget('foo', "Foo"),
        create_widget('bar', "Bar"),
        create_widget(None, "Public"),
    ))
    db.session.commit()


# -----------------------------------------------------------------------------


def test_list(client):
    response = client.get('/api/widgets?user_id=foo')
    assert helpers.get_data(response) == [
        {
            'id': '1',
            'owner_id': 'foo',
            'name': "Foo",
        },
        {
            'id': '3',
            'owner_id': None,
            'name': "Public",
        },
    ]


def test_retrieve(client):
    response = client.get('/api/widgets/1?user_id=foo')
    assert response.status_code == 200


def test_create(client):
    response = helpers.request(
        client,
        'POST', '/api/widgets?user_id=foo',
        {
            'owner_id': 'foo',
            'name': "Created",
        },
    )
    assert response.status_code == 201


def test_update(client):
    response = helpers.request(
        client,
        'PATCH', '/api/widgets/1?user_id=foo',
        {
            'id': '1',
            'owner_id': 'foo',
            'name': "Updated",
        },
    )
    assert response.status_code == 204


def test_delete(client):
    response = client.delete('/api/widgets/1?user_id=foo')
    assert response.status_code == 204


# -----------------------------------------------------------------------------


def test_error_unauthenticated(client):
    response = client.get('/api/widgets')
    assert response.status_code == 401

    assert helpers.get_errors(response) == [{
        'code': 'invalid_credentials.missing',
    }]


def test_error_retrieve_unauthorized(client):
    response = client.get('/api/widgets/1?user_id=bar')
    assert response.status_code == 404


def test_error_create_unauthorized(client):
    response = helpers.request(
        client,
        'POST', '/api/widgets?user_id=bar',
        {
            'owner_id': 'foo',
            'name': "Created",
        },
    )
    assert response.status_code == 403

    assert helpers.get_errors(response) == [{
        'code': 'invalid_user'
    }]


def test_error_update_unauthorized(client):
    not_found_response = helpers.request(
        client,
        'PATCH', '/api/widgets/1?user_id=bar',
        {
            'id': '1',
            'owner_id': 'bar',
            'name': "Updated",
        },
    )
    assert not_found_response.status_code == 404

    forbidden_response = helpers.request(
        client,
        'PATCH', '/api/widgets/3?user_id=bar',
        {
            'id': '3',
            'owner_id': 'bar',
            'name': "Updated",
        },
    )
    assert forbidden_response.status_code == 403

    assert helpers.get_errors(forbidden_response) == [{
        'code': 'invalid_user'
    }]


def test_error_delete_unauthorized(client):
    not_found_response = client.delete('/api/widgets/1?user_id=bar')
    assert not_found_response.status_code == 404

    forbidden_response = client.delete('/api/widgets/3?user_id=bar')
    assert forbidden_response.status_code == 403

    assert helpers.get_errors(forbidden_response) == [{
        'code': 'invalid_user'
    }]
