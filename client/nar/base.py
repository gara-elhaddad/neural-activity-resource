"""

"""

import sys
from functools import wraps
import logging
from six import with_metaclass
from .errors import ResourceExistsError


logger = logging.getLogger("nar")


registry = {}

# todo: add namespaces to avoid name clashes, e.g. "Person" exists in several namespaces
def register_class(target_class):
    registry[target_class.__name__] = target_class


def lookup(class_name):
    return registry[class_name]


class Registry(type):
    """Metaclass for registering Knowledge Graph classes"""

    def __new__(meta, name, bases, class_dict):
        cls = type.__new__(meta, name, bases, class_dict)
        register_class(cls)
        return cls


#class KGObject(object, metaclass=Registry):
class KGObject(with_metaclass(Registry, object)):
    """Base class for Knowledge Graph objects"""
    cache = {}

    def __init__(self, id=None, instance=None, **properties):
        for key, value in properties.items():
            if key not in self.property_names:
                raise TypeError("{self.__class__.__name__} got an unexpected keyword argument '{key}'".format(self=self, key=key))
            else:
                setattr(self, key, value)
        self.id = id
        self.instance = instance

    def __repr__(self):
        return ('{self.__class__.__name__}('
                '{self.name!r} {self.id!r})'.format(self=self))

    @classmethod
    def from_kg_instance(self, instance, client):
        raise NotImplementedError("To be implemented by child class")

    @classmethod
    def from_uri(cls, uri, client):
        return cls.from_kg_instance(client.instance_from_full_uri(uri),
                                    client)

    @classmethod
    def list(cls, client, size=100, **filters):
        """List all objects of this type in the Knowledge Graph"""
        return client.list(cls, size=size)

    def exists(self, client):
        """Check if this object already exists in the KnowledgeGraph"""
        # Note that this default implementation should in
        # many cases be over-ridden.
        if self.id:
            return True
        else:
            context = {"schema": "http://schema.org/"},
            query_filter = {
                "path": "schema:name",
                "op": "eq",
                "value": self.name
            }
            response = client.filter_query(self.path, query_filter, context)
            if response:
                self.id = response[0].data["@id"]
            return bool(response)

    def _save(self, data, client, exists_ok=True):
        """docstring"""
        if self.id:
            # instance.data should be identical to data at this point
            self.instance = client.update_instance(self.instance)
            logger.info("Updating {self.instance.id}".format(self=self))
        else:
            if self.exists(client):
                if exists_ok:
                    logger.info("Not updating {self.__class__.__name__}, already exists (id={self.id})".format(self=self))
                    return
                else:
                    raise ResourceExistsError("Already exists in the Knowledge Graph: {self!r}".format(self=self))
            instance = client.create_new_instance(self.__class__.path, data)
            self.id = instance.data["@id"]
            self.instance = instance
            KGObject.cache[self.id] = self

    @classmethod
    def by_name(cls, name, client):
        return client.by_name(cls, name)

    @property
    def rev(self):
        if self.instance:
            return self.instance.data.get("nxv:rev", None)
        else:
            return None


def cache(f):
    @wraps(f)
    def wrapper(cls, instance, client):
        if instance.data["@id"] in KGObject.cache:
            obj = KGObject.cache[instance.data["@id"]]
            #print(f"Found in cache: {obj.id}")
            return obj
        else:
            obj = f(cls, instance, client)
            KGObject.cache[obj.id] = obj
            #print(f"Added to cache: {obj.id}")
            return obj
    return wrapper


class OntologyTerm(object):
    """docstring"""

    def __init__(self, label, iri=None):
        self.label = label
        self.iri = iri or self.iri_map[label]

    def __repr__(self):
        #return (f'{self.__class__.__name__}('
        #        f'{self.label!r}, {self.iri!r})')
        return ('{self.__class__.__name__}('
                '{self.label!r}, {self.iri!r})'.format(self=self))
    
    def to_jsonld(self):
        return {'@id': self.iri,
                'label': self.label}
    
    @classmethod
    def from_jsonld(cls, data):
        if data is None:
            return None
        return cls(data["label"], data["@id"])


class KGProxy(object):
    """docstring"""

    def __init__(self, cls, uri):
        if isinstance(cls, str):
            self.cls = lookup(cls)
        else:
            self.cls = cls
        self.id = uri

    @property
    def type(self):
        return self.cls.type

    def resolve(self, client):
        """docstring"""
        if self.id in KGObject.cache:
            return KGObject.cache[self.id]
        else:
            obj = self.cls.from_uri(self.id, client)
            KGObject.cache[self.id] = obj
            return obj

    def __repr__(self):
        #return (f'{self.__class__.__name__}('
        #        f'{self.cls!r}, {self.id!r})')
        return ('{self.__class__.__name__}('
                '{self.cls!r}, {self.id!r})'.format(self=self))


class KGQuery(object):
    """docstring"""

    def __init__(self, cls, filter, context):
        if isinstance(cls, str):
            self.cls = lookup(cls)
        else:
            self.cls = cls
        self.filter = filter
        self.context = context

    def resolve(self, client):
        instances = client.filter_query(
            path=self.cls.path,
            filter=self.filter,
            context=self.context
        )
        objects = [self.cls.from_kg_instance(instance, client)
                   for instance in instances]
        for obj in objects:
            KGObject.cache[obj.id] = obj
        if len(instances) == 1:
            return objects[0]
        else:
            return objects


def build_kg_object(cls, data):
    """ 
    Build a KGObject, a KGProxy, or a list of such, based on the data provided.

    This takes care of the JSON-LD quirk that you get a list if there are multiple
    objects, but you get the object directly if there is only one.

    Returns `None` if data is None.
    """

    if data is None:
        return None
    
    if not isinstance(data, list):
        if not isinstance(data, dict):
            raise ValueError("data must be a list or dict")
        if "@list" in data:
            assert len(data) == 1
            data = data["@list"]
        else:
            data = [data]

    objects = []
    for item in data:
        if issubclass(cls, OntologyTerm):
            obj = cls.from_jsonld(item)
        elif issubclass(cls, KGObject):
            obj = KGProxy(cls, item["@id"])
        else:
            raise ValueError("cls must be a KGObject or OntologyTerm")
        objects.append(obj)

    if len(objects) == 1:
        return objects[0]
    else:
        return objects


def as_list(obj):
    try:
        L = list(obj)
    except TypeError:
        L = [obj]
    return L
