from dateutil.relativedelta import relativedelta
from dateutil.rrule import rrule, DAILY, MO, TU, WE, TH, FR

from datetime import datetime, time

from trytond.model import Workflow, ModelSQL, ModelSingleton, ModelView, fields
from trytond.modules.calendar.calendar_ import Event
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval, If, In
from trytond.transaction import Transaction

__all__ = ['ResourceConfiguration', 'ResourceConfigIrModel', 'Company',
    'Resource', 'ResourceBooking']


class ResourceConfiguration(ModelSingleton, ModelSQL, ModelView):
    'Resource Configuration'
    __name__ = 'resource.configuration'

    documents = fields.Many2Many('resource.configuration-ir.model',
        'configuration', 'document', 'Documents')


class ResourceConfigIrModel(ModelSQL):
    'ResourceConfig - Ir Model'
    __name__ = 'resource.configuration-ir.model'

    document = fields.Many2One('ir.model', 'Document', ondelete='CASCADE',
        required=True, select=True)
    configuration = fields.Many2One('resource.configuration', 'Configuration',
        ondelete='CASCADE', required=True, select=True)


class Company:
    __name__ = 'company.company'
    __metaclass__ = PoolMeta

    day_starts = fields.Time('Day Start', help='The hour on which the working '
        'day starts.', required=True)
    day_ends = fields.Time('Day Ends', help='The hour on which the working '
        'day ends.', required=True)

    @staticmethod
    def default_day_starts():
        return time(9, 00)

    @staticmethod
    def default_day_ends():
        return time(17, 00)


class Resource(ModelSQL, ModelView):
    'Resource'
    __name__ = 'resource.resource'

    name = fields.Char('Name', required=True)
    active = fields.Boolean('Active')
    employee = fields.Many2One('company.employee', 'Employee', select=True)
    calendar = fields.Many2One('calendar.calendar', 'Calendar', required=True,
     select=True, ondelete='CASCADE')
    bookings = fields.One2Many('resource.booking', 'resource', 'Bookings',
        context={
            'calendar': Eval('calendar'),
            },
        depends=['calendar'])
    type = fields.Selection([
        ('human', 'Human'),
        ], 'Type', required=True, select=True,)
    company = fields.Many2One('company.company', 'Company', required=True,
        domain=[
            ('id', If(In('company', Eval('context', {})), '=', '!='),
                Eval('context', {}).get('company', -1)),
            ])

    @staticmethod
    def default_active():
        return True

    @staticmethod
    def default_company():
        return Transaction().context.get('company')

    @staticmethod
    def default_type():
        return 'human'

    def default_daily_schedule(self, date):
        start = datetime.combine(date, self.company.day_starts)
        end = datetime.combine(date, self.company.day_ends)
        return (start, end, self.company.hours_per_work_day)

    def get_busy_hours(self, start, end):
        pool = Pool()
        Booking = pool.get('resource.booking')
        start = datetime.combine(start, datetime.min.time())
        end = datetime.combine(end, datetime.max.time())
        events = Booking.search([
                ('dtstart', '>=', start),
                ('dtend', '<=', end),
                ('resource', '=', self.id),
                ('state', '!=', 'cancel'),
                ])

        dates = list(rrule(DAILY, bysetpos=1, dtstart=start, until=end,
                byweekday=[MO, TU, WE, TH, FR]))
        res = {}
        for date in dates:
            res[date.date()] = []

        for event in events:
            date = event.dtstart.date()
            res[date] += [event.interval_values()]

        dates = res.keys()
        dates.sort()
        return res

    def fill_timetable(self, interval1, interval2, kind=0):
        sd1, fd1, _ = interval1
        sd2, fd2, _ = interval2

        missing_interval = []
        if kind == 1:
            if sd2 > sd1:
                hours = (sd2 - sd1).total_seconds() / 3600
                missing_interval = [(sd1, sd2, hours)]
        elif kind == 2:
            if fd2 < fd1:
                hours = (fd1 - fd2).total_seconds() / 3600
                missing_interval = [(fd2, fd1, hours)]
        elif fd1 != sd2:
            hours = (fd2 - sd1).total_seconds() / 3600
            missing_interval = [(fd2, sd1, hours)]

        return missing_interval

    def timetable(self, date, busy_intervals):

        schedule = self.default_daily_schedule(date)
        if busy_intervals == []:
            return [schedule], [schedule]
        busy_intervals.sort(key=lambda r: r[0])

        init = self.fill_timetable(schedule, busy_intervals[0], 1)
        free = init
        timetable = init
        current = busy_intervals[0]
        for event in busy_intervals[1:]:
            miss = self.fill_timetable(current, event)
            free += miss
            timetable += miss
            current = event

        finish = self.fill_timetable(schedule, current, 2)
        free = finish
        timetable += finish
        return timetable, free

    def find_free_time(self, start, end, min_hours=None):
        busy = self.get_busy_hours(start, end)
        res = {}
        for day in res.keys():
            res[day] = []
        res = {}.fromkeys(busy.keys())

        dates = busy.keys()
        dates.sort()
        for bdate in dates:
            busy_interval = busy[bdate]
            timetable, free = self.timetable(bdate, busy_interval)
            res[bdate] = free
        #Only return the dates that have free time
        result = res.copy()
        for k, v in res.iteritems():
            if not v:
                del result[k]
        return result

    @staticmethod
    def default_ahead():
        return relativedelta(days=7)

    def book_hours(self, date, hours, ahead=None, min_hours=None):
        ahead_search = ahead or self.default_ahead()
        ahead_date = date + ahead_search
        free = self.find_free_time(date, ahead_date, min_hours)
        pending_hours = hours
        bookings = []

        free_time = []
        dates = free.keys()
        dates.sort()
        for k in dates:
            free_time += free[k]

        for t in free_time:
            start, end, free_hours = t
            assign = free_hours
            if pending_hours <= free_hours:
                assign = pending_hours

            pending_hours -= assign
            bookings.append((start, start + relativedelta(hours=assign),
                assign))

            if pending_hours == 0:
                break
        return bookings

    def book_interval(self, bookings):
        if not bookings:
            return (None, None)

        start = min([x[0] for x in bookings])
        end = max([x[1] for x in bookings])
        return (start, end)

    def unbook(self, start, end):
        pass

    def book(self, intervals, document, status='tentative'):
        pool = Pool()
        Event = pool.get('resource.booking')
        create_bookings = []
        for interval in intervals:
            i = {
                'dtstart': interval[0],
                'dtend': interval[1],
                'calendar': self.calendar.id,
                'resource': self.id,
                'status': status,
                'state': 'draft',
                'document': document,
            }
            create_bookings.append(i)
        return Event.create(create_bookings)

    @classmethod
    def get_free_resource(cls, date, hours, domain=None):
        '''
        Finds free resourece for the given date and the given number of hours.
        The domain param allows to restric the possible resources to search.
        '''
        if domain is None:
            domain = []
        start_per_resource = []
        for resource in cls.search(domain):
            free_times = resource.find_free_time(date,
                date + cls.default_ahead(), hours)
            if free_times:
                min_date = min(free_times.keys())
                start_per_resource.append((free_times[min_date][0], resource))
        if not start_per_resource:
            return None
        return min(start_per_resource, key=lambda a: a[0])[1]


class ResourceBooking(Workflow, Event):
    'Resource Book'
    __name__ = 'resource.booking'

    resource = fields.Many2One('resource.resource', 'Resource', required=True,
            select=True, ondelete='CASCADE')
    document = fields.Reference('Document', selection='get_document',
        select=True)
    state = fields.Selection([
            ('draft', 'Draft'),
            ('confirmed', 'Confirmed'),
            ('canceled', 'Canceled'),
            ], 'State', required=True, select=True, readonly=True)

    hours = fields.Function(fields.Float('Hours', digits=(16, 2)),
        'get_hours')

    @classmethod
    def __setup__(cls):
        super(ResourceBooking, cls).__setup__()
        cls._order.insert(0, ('sequence', 'ASC'))
        cls._error_messages.update({
                'delete_cancel': ('Booking "%s" must be cancelled before '
                    'deletion.'),
                })
        cls._transitions |= set([
                ('draft', 'confirmed'),
                ('draft', 'canceled'),
                ('confirmed', 'canceled'),
                ('canceled', 'draft'),
                ])
        cls._buttons.update({
                'draft': {
                    'invisible': Eval('state') != 'canceled',
                    'icon': 'tryton-clear',
                    },
                'confirm': {
                    'invisible': Eval('state') != 'draft',
                    'icon': 'tryton-ok',
                    },
                'cancel': {
                    'invisible': Eval('state') == 'canceled',
                    'icon': 'tryton-cancel',
                    },
                })

    @staticmethod
    def order_sequence(tables):
        table, _ = tables[None]
        return [table.sequence == None, table.sequence]

    @staticmethod
    def default_state():
        return 'draft'

    @staticmethod
    def default_calendar():
        return Transaction().context.get('calendar')

    def get_rec_name(self, name=None):
        if self.dtend:
            dates = '(%s - %s)' % (self.dtstart, self.dtend)
        else:
            dates = '(%s)' % (self.dtstart)
        name = '%s %s' % (self.resource.rec_name, dates)
        return name

    def get_hours(self, name=None):
        if not self.dtend:
            return 0
        return (self.dtend - self.dtstart).total_seconds() / 3600

    def interval_values(self):
        start = self.dtstart
        end = self.dtend
        hours = self.hours
        return (start, end, hours)

    @classmethod
    def get_document(cls):
        pool = Pool()
        Config = pool.get('resource.configuration')
        config, = Config.search([])
        res = [('', '')]
        for document in config.documents:
            res.append((document.model, document.name))
        return res

    @classmethod
    def delete(cls, bookings):
        for book in bookings:
            if book.state != 'canceled':
                cls.raise_user_error('delete_cancel', (book.rec_name,))
        super(ResourceBooking, cls).delete(bookings)

    @classmethod
    @ModelView.button
    @Workflow.transition('draft')
    def draft(cls, bookings):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('confirmed')
    def confirm(cls, bookings):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('canceled')
    def cancel(cls, bookings):
        pass
